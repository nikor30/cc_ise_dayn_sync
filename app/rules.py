"""Mapping-rule engine: first enabled rule (by priority) whose criteria all match wins."""
import logging
import re

from .db import SessionLocal
from .models import MappingRule, SiteMapping

log = logging.getLogger(__name__)

DEVICE_TYPE_ROOT = "Device Type"
LOCATION_ROOT = "Location"


def _regex_match(pattern: str, value: str) -> bool:
    """Empty pattern = wildcard; matching is case-insensitive (hostnames, sites
    etc. differ in casing between CC and ISE). Falls back to exact compare on
    invalid regex."""
    if not pattern:
        return True
    try:
        return re.search(pattern, value or "", re.IGNORECASE) is not None
    except re.error:
        return pattern.lower() == (value or "").lower()


def _tag_match(pattern: str, tags: list[str]) -> bool:
    """Exact tag name or regex against any of the device's tags (case-insensitive)."""
    if not pattern:
        return True
    if pattern.lower() in (t.lower() for t in tags):
        return True
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return False
    return any(rx.search(t) for t in tags)


def rule_matches(rule: MappingRule, device: dict) -> bool:
    """device: {hostname, family, series, platform, site, tags[]}"""
    return (
        _tag_match(rule.match_tag, device.get("tags") or [])
        and _regex_match(rule.match_hostname, device.get("hostname", ""))
        and _regex_match(rule.match_family, device.get("family", ""))
        and _regex_match(rule.match_series, device.get("series", ""))
        and _regex_match(rule.match_platform, device.get("platform", ""))
        and _regex_match(rule.match_site, device.get("site", ""))
    )


def load_rules() -> list[MappingRule]:
    with SessionLocal() as s:
        return list(s.query(MappingRule).order_by(MappingRule.priority, MappingRule.id).all())


def load_site_mappings() -> list[SiteMapping]:
    with SessionLocal() as s:
        return list(s.query(SiteMapping).order_by(SiteMapping.priority, SiteMapping.id).all())


def evaluate(device: dict, rules: list[MappingRule] | None = None) -> MappingRule | None:
    """Return the first matching enabled rule, or None."""
    for rule in rules if rules is not None else load_rules():
        if rule.enabled and rule_matches(rule, device):
            return rule
    return None


def site_to_location(site: str, mappings: list[SiteMapping] | None = None) -> str | None:
    """Resolve a CC site hierarchy to an ISE Location NDG via the mapping table."""
    for m in mappings if mappings is not None else load_site_mappings():
        if _regex_match(m.site_pattern, site):
            return m.location_ndg
    return None


def derived_location_name(site: str) -> str:
    """Auto-create name: 'Global/DE/Schierling' -> 'Location#All Locations#DE#Schierling'."""
    parts = [p for p in (site or "").split("/") if p]
    if parts and parts[0].lower() == "global":
        parts = parts[1:]
    if not parts:
        return ""
    return "Location#All Locations#" + "#".join(parts)


def resolve_targets(rule: MappingRule, device: dict,
                    mappings: list[SiteMapping] | None = None) -> dict:
    """Compute the desired NDGs for a device under a rule.

    Returns {"device_type": str|None, "location": str|None,
             "create_location": bool, "notes": [..]}  (None = leave unchanged)
    """
    out = {"device_type": rule.device_type_ndg or None, "location": None,
           "create_location": False, "notes": []}
    if rule.location_mode == "fixed":
        out["location"] = rule.location_ndg or None
        return out
    # derive from CC site
    site = device.get("site", "")
    mapped = site_to_location(site, mappings)
    if mapped:
        out["location"] = mapped
        return out
    # no site mapping matched -> per-rule fallback
    if rule.derive_fallback == "default" and rule.location_ndg:
        out["location"] = rule.location_ndg
        out["notes"].append(f"no site mapping for '{site}', used default location")
    elif rule.derive_fallback == "create" or rule.auto_create_location:
        name = derived_location_name(site)
        if name:
            out["location"] = name
            out["create_location"] = True
            out["notes"].append(f"no site mapping for '{site}', will auto-create '{name}'")
        else:
            out["notes"].append(f"no site mapping and no usable site path ('{site}'); location skipped")
    else:
        out["notes"].append(f"no site mapping for '{site}'; location left unchanged")
    return out


def merge_ndgs(current: list[str], device_type: str | None, location: str | None) -> list[str]:
    """Replace only the Device Type#/Location# entries; keep IPSEC# and other dimensions."""
    kept = []
    for entry in current or []:
        root = entry.split("#", 1)[0]
        if device_type is not None and root == DEVICE_TYPE_ROOT:
            continue
        if location is not None and root == LOCATION_ROOT:
            continue
        kept.append(entry)
    if device_type is not None:
        kept.append(device_type)
    if location is not None:
        kept.append(location)
    return kept


def is_default_ndgs(ndgs: list[str]) -> bool:
    """True if the device still sits on the root/default Device Type or Location NDG."""
    return ("Device Type#All Device Types" in (ndgs or [])
            or "Location#All Locations" in (ndgs or []))
