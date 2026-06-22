"""Unit coverage for the ``doxie`` branch of ``_row_details``.

Pure / non-integration: exercises the per-row ``details`` builder for the
Doxie chatbot event type directly, with no DB, HTTP, Gemini, or SEC. This
keeps the default ``pytest`` run (which deselects ``-m integration``)
covering the new branch even though the DB-backed feed test in
``test_users_admin_doxie_activities.py`` is integration-marked.

The chatbot UNION branch JSON-encodes ``{path, title, preview}`` into the
``audit_details`` Text column; ``_row_details`` must parse that back into a
dict for the FE tooltip (and tolerate missing / malformed payloads the
same way the other JSON-backed branches do).
"""

from __future__ import annotations

import json

from app.api.v1.endpoints.users_admin import _row_details


def _details(audit_details: str | None) -> dict | None:
    """Call ``_row_details`` for a doxie row; all firm columns are NULL."""
    return _row_details(
        event_type="doxie",
        audit_details=audit_details,
        list_name=None,
        visit_count=None,
        send_status=None,
        send_error=None,
        send_subject=None,
    )


def test_doxie_row_details_parses_json_payload() -> None:
    payload = json.dumps(
        {
            "path": "/master-list",
            "title": "Broker Dealers",
            "preview": "Which firms cleared through Pershing?",
        }
    )
    result = _details(payload)
    assert result == {
        "path": "/master-list",
        "title": "Broker Dealers",
        "preview": "Which firms cleared through Pershing?",
    }


def test_doxie_row_details_passes_through_null_context_fields() -> None:
    # page_context_path / title are nullable on the message; the SQL
    # json_build_object still emits the keys (with null values).
    payload = json.dumps({"path": None, "title": None, "preview": "Hi Doxie"})
    result = _details(payload)
    assert result == {"path": None, "title": None, "preview": "Hi Doxie"}


def test_doxie_row_details_none_audit_details_returns_none() -> None:
    assert _details(None) is None


def test_doxie_row_details_malformed_json_returns_none() -> None:
    # Defensive: a non-JSON / truncated payload must not raise.
    assert _details("{not valid json") is None


def test_doxie_row_details_non_object_json_returns_none() -> None:
    # A JSON array (not an object) is not a valid details dict.
    assert _details(json.dumps(["preview"])) is None
