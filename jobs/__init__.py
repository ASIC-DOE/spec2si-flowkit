"""Cluster job-status & observability subsystem (docs/job_status_plan.md).

Phase 0 (shipped): `remote` -- a transport that cannot be corrupted.
Later phases add `runjob` (self-reporting sidecar), `progress` (shared
extractors + rate-based ETA), and events/notification. See the plan for
the architecture and the phased rollout.
"""
from .remote import (  # noqa: F401
    Transport, Result, KNOWN, UNKNOWN, STALE,
    normalize_script, reader_hash, DEFAULT_HOST,
)

__all__ = [
    "Transport", "Result", "KNOWN", "UNKNOWN", "STALE",
    "normalize_script", "reader_hash", "DEFAULT_HOST",
]
