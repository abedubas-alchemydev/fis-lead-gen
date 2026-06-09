"""Small helper for writing ``audit_log`` rows.

Centralises the ``AuditLog(...) -> db.add -> flush/commit`` pattern that was
previously inlined at each call site (see ``api/v1/endpoints/pipeline.py``).
Keeping it in one place means new audit actions stay consistent and the
JSON-encoding of ``details`` isn't re-derived everywhere.

``action`` is intentionally an open string — the audit log is not a closed
enum (pipelines, Fresh Regen, the approval gate, and FINRA lookups all write
distinct action strings).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    user_id: str | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = False,
) -> None:
    """Append an ``audit_log`` row.

    ``user_id`` is the acting user (``None`` for background/system actions).
    ``details`` is JSON-serialised when present, left ``NULL`` otherwise.

    By default the row is ``flush``-ed only, so it joins the caller's
    surrounding transaction and is persisted by the caller's own ``commit``.
    Pass ``commit=True`` when the audit row must survive on its own (e.g. a
    paper trail that must outlive a subsequent non-transactional side effect).
    """
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            details=json.dumps(details) if details is not None else None,
        )
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
