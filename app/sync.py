"""Core sync pipeline: CC device -> rule evaluation -> ISE NDG update."""
import asyncio
import logging

from .clients.catalyst import CatalystClient, CatalystError
from .clients.ise import ISEClient, ISEError
from . import audit, blacklist, rules
from .settings_store import retry_schedule

log = logging.getLogger(__name__)


async def enrich_from_cc(cc: CatalystClient, ref: dict) -> dict | None:
    """Resolve a device reference {id|hostname|ip} into the full match context."""
    dev = None
    if ref.get("id"):
        dev = await cc.get_device_by_id(ref["id"])
    if dev is None and (ref.get("hostname") or ref.get("ip")):
        dev = await cc.find_device(hostname=ref.get("hostname", ""), ip=ref.get("ip", ""))
    if dev is None:
        return None
    dev["site"] = await cc.get_site(dev["id"]) if dev.get("id") else ""
    try:
        dev["tags"] = await cc.get_device_tags(dev["id"]) if dev.get("id") else []
    except CatalystError as exc:
        log.warning("tag lookup failed for %s: %s", dev.get("hostname"), exc)
        dev["tags"] = []
    return dev


def plan_for_device(device: dict) -> dict:
    """Evaluate rules for an enriched CC device. Pure function of DB state."""
    rule = rules.evaluate(device)
    if rule is None:
        return {"matched": False, "device": device}
    targets = rules.resolve_targets(rule, device)
    return {
        "matched": True,
        "device": device,
        "rule": {"id": rule.id, "description": rule.description, "priority": rule.priority},
        "targets": targets,
    }


async def find_in_ise_with_retry(ise: ISEClient, hostname: str, ip: str,
                                 audit_id: int | None = None) -> dict | None:
    """CC may not have pushed the device to ISE yet — retry with backoff."""
    dev = await ise.find_device(name=hostname, ip=ip)
    if dev is not None:
        return dev
    for delay in retry_schedule():
        if audit_id:
            audit.update_status(audit_id, "retrying",
                                f"device not in ISE yet, retrying in {delay}s")
        log.info("device %s not found in ISE; retrying in %ds", hostname, delay)
        await asyncio.sleep(delay)
        dev = await ise.find_device(name=hostname, ip=ip)
        if dev is not None:
            return dev
    return None


async def apply_to_ise(ise: ISEClient, ise_device: dict, targets: dict) -> tuple[list, list]:
    """Replace only the Device Type#/Location# NDG entries and PUT the full object."""
    old_ndgs = list(ise_device.get("NetworkDeviceGroupList") or [])
    if targets.get("create_location") and targets.get("location"):
        try:
            result = await ise.create_ndg(targets["location"], root="Location")
            log.info("auto-create Location NDG '%s': %s", targets["location"], result)
        except ISEError as exc:
            raise ISEError(f"auto-create of Location NDG failed: {exc}")
    new_ndgs = rules.merge_ndgs(old_ndgs, targets.get("device_type"), targets.get("location"))
    if sorted(new_ndgs) == sorted(old_ndgs):
        return old_ndgs, old_ndgs  # nothing to do
    updated = dict(ise_device)
    updated["NetworkDeviceGroupList"] = new_ndgs
    await ise.update_device(updated)
    return old_ndgs, new_ndgs


async def process_device_event(ref: dict, trigger: str = "webhook", raw=None,
                               retry_lookup: bool = True) -> dict:
    """Full pipeline for one device. `ref` = {id|hostname|ip}.
    `retry_lookup=False` fails fast when the device is not in ISE (used by the
    GUI force-sync so the request returns immediately)."""
    label = ref.get("hostname") or ref.get("ip") or ref.get("id") or "?"
    if blacklist.is_blacklisted(ref.get("hostname", ""), ref.get("ip", "")):
        audit.record(trigger, "skipped", device_name=ref.get("hostname", label),
                     device_ip=ref.get("ip", ""), message="device is blacklisted", raw=raw)
        return {"status": "skipped", "message": "device is blacklisted"}
    try:
        async with CatalystClient() as cc:
            device = await enrich_from_cc(cc, ref)
    except CatalystError as exc:
        audit.record(trigger, "failed", device_name=label, message=f"Catalyst Center error: {exc}", raw=raw)
        return {"status": "failed", "message": str(exc)}
    if device is None:
        audit.record(trigger, "failed", device_name=label,
                     message="device not found in Catalyst Center inventory", raw=raw)
        return {"status": "failed", "message": "device not found in Catalyst Center"}

    name, ip = device.get("hostname", ""), device.get("ip", "")
    if blacklist.is_blacklisted(name, ip):  # re-check with CC-resolved identity
        audit.record(trigger, "skipped", device_name=name, device_ip=ip,
                     message="device is blacklisted", raw=raw)
        return {"status": "skipped", "message": "device is blacklisted"}
    plan = plan_for_device(device)
    if not plan["matched"]:
        audit.record(trigger, "skipped", device_name=name, device_ip=ip,
                     message="no mapping rule matched", raw=raw)
        return {"status": "skipped", "message": "no rule matched", "device": device}

    targets = plan["targets"]
    rule_label = f"#{plan['rule']['id']} {plan['rule']['description']}".strip()
    if not targets.get("device_type") and not targets.get("location"):
        audit.record(trigger, "skipped", device_name=name, device_ip=ip, rule=rule_label,
                     message="rule matched but resolved no NDG changes; " +
                             "; ".join(targets.get("notes", [])), raw=raw)
        return {"status": "skipped", "message": "rule resolved no targets", "plan": plan}

    audit_id = audit.record(trigger, "retrying", device_name=name, device_ip=ip,
                            rule=rule_label, message="processing", raw=raw)
    try:
        async with ISEClient() as ise:
            if retry_lookup:
                ise_device = await find_in_ise_with_retry(ise, name, ip, audit_id)
            else:
                ise_device = await ise.find_device(name=name, ip=ip)
            if ise_device is None:
                audit.update_status(audit_id, "failed",
                                    "device never appeared in ISE within the retry window")
                return {"status": "failed", "message": "device not found in ISE"}
            old_ndgs, new_ndgs = await apply_to_ise(ise, ise_device, targets)
    except ISEError as exc:
        audit.update_status(audit_id, "failed", f"ISE error: {exc}")
        return {"status": "failed", "message": str(exc)}

    notes = "; ".join(targets.get("notes", []))
    if old_ndgs == new_ndgs:
        audit.update_status(audit_id, "success",
                            ("already compliant. " + notes).strip(), new_ndgs=new_ndgs)
        _set_old(audit_id, old_ndgs)
        return {"status": "success", "message": "already compliant", "plan": plan}
    audit.update_status(audit_id, "success", ("updated. " + notes).strip(), new_ndgs=new_ndgs)
    _set_old(audit_id, old_ndgs)
    log.info("updated %s in ISE: %s -> %s", name, old_ndgs, new_ndgs)
    return {"status": "success", "old_ndgs": old_ndgs, "new_ndgs": new_ndgs, "plan": plan}


def _set_old(audit_id: int, old_ndgs: list):
    import json
    from .db import SessionLocal
    from .models import AuditLog
    with SessionLocal() as s:
        entry = s.get(AuditLog, audit_id)
        if entry:
            entry.old_ndgs = json.dumps(old_ndgs)
            s.commit()


async def dry_run(ref: dict) -> dict:
    """Simulate: which rule matches and what would be written — no writes."""
    async with CatalystClient() as cc:
        device = await enrich_from_cc(cc, ref)
    if device is None:
        return {"status": "failed", "message": "device not found in Catalyst Center"}
    plan = plan_for_device(device)
    result = {"status": "dry-run", **plan}
    if plan["matched"]:
        try:
            async with ISEClient() as ise:
                ise_dev = await ise.find_device(name=device.get("hostname", ""),
                                                ip=device.get("ip", ""))
            if ise_dev:
                old = list(ise_dev.get("NetworkDeviceGroupList") or [])
                result["ise_current_ndgs"] = old
                result["ise_projected_ndgs"] = rules.merge_ndgs(
                    old, plan["targets"].get("device_type"), plan["targets"].get("location"))
            else:
                result["ise_current_ndgs"] = None
                result["message"] = "device not (yet) present in ISE"
        except ISEError as exc:
            result["message"] = f"ISE lookup failed: {exc}"
    audit.record("dry-run", "dry-run", device_name=device.get("hostname", ""),
                 device_ip=device.get("ip", ""),
                 rule=(f"#{plan['rule']['id']} {plan['rule']['description']}"
                       if plan.get("matched") else ""),
                 old_ndgs=result.get("ise_current_ndgs") or [],
                 new_ndgs=result.get("ise_projected_ndgs") or [],
                 message="simulation only — nothing written")
    return result
