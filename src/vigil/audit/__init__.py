"""Tamper-evident audit trail. Assumed jurisdiction: India DPDP Act 2023 + ABDM."""
from vigil.audit.log import AuditEntry, AuditLog, TamperError, record_assessment, record_override

__all__ = ["AuditEntry", "AuditLog", "TamperError", "record_assessment", "record_override"]
