"""ORM models."""
from datetime import datetime, timezone

from sqlalchemy import String, Integer, Boolean, Text, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)


class NDGCache(Base):
    __tablename__ = "ndg_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    ndg_type: Mapped[str] = mapped_column(String(32), index=True)  # "device_type" | "location" | "other"
    ise_id: Mapped[str] = mapped_column(String(64), default="")
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MappingRule(Base):
    __tablename__ = "mapping_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Match criteria (regex unless noted; empty = wildcard; all AND-ed)
    match_tag: Mapped[str] = mapped_column(String(255), default="")
    match_hostname: Mapped[str] = mapped_column(String(255), default="")
    match_family: Mapped[str] = mapped_column(String(255), default="")
    match_series: Mapped[str] = mapped_column(String(255), default="")
    match_platform: Mapped[str] = mapped_column(String(255), default="")
    match_site: Mapped[str] = mapped_column(String(255), default="")
    # Actions
    device_type_ndg: Mapped[str] = mapped_column(String(255), default="")
    location_mode: Mapped[str] = mapped_column(String(16), default="fixed")  # "fixed" | "derive"
    location_ndg: Mapped[str] = mapped_column(String(255), default="")  # fixed value / default fallback
    derive_fallback: Mapped[str] = mapped_column(String(16), default="skip")  # "skip" | "default" | "create"
    auto_create_location: Mapped[bool] = mapped_column(Boolean, default=False)


class SiteMapping(Base):
    __tablename__ = "site_mappings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    site_pattern: Mapped[str] = mapped_column(String(255))  # regex on CC site hierarchy
    location_ndg: Mapped[str] = mapped_column(String(255))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="webhook")  # webhook|reconcile|manual|dry-run|system
    device_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    device_ip: Mapped[str] = mapped_column(String(64), default="")
    rule: Mapped[str] = mapped_column(String(255), default="")
    old_ndgs: Mapped[str] = mapped_column(Text, default="")  # JSON list
    new_ndgs: Mapped[str] = mapped_column(Text, default="")  # JSON list
    status: Mapped[str] = mapped_column(String(32), default="", index=True)  # success|retrying|failed|skipped|dry-run|info
    message: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[str] = mapped_column(Text, default="")  # raw payload / HTTP details (JSON)


Index("ix_audit_ts_status", AuditLog.ts, AuditLog.status)


class ReconcileRun(Base):
    __tablename__ = "reconcile_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|done|failed
    scanned: Mapped[int] = mapped_column(Integer, default=0)
    fixed: Mapped[int] = mapped_column(Integer, default=0)
    unmatched: Mapped[int] = mapped_column(Integer, default=0)
    not_found_in_cc: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
