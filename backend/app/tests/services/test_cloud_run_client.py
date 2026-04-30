"""Unit tests for :mod:`app.services.cloud_run_client`.

Covers the three behavioural guarantees of the wrapper:

* ``ALLOWED_ENV_VARS`` is enforced — any env name not in the
  allowlist raises ``ValueError`` **before** any Cloud Run RPC.
  This is the application-layer guard that constrains the
  ``roles/run.developer`` IAM grant on the runtime SA, so it gets
  exercised explicitly here (including a defensive check against
  obvious dangerous names).
* The happy path: ``get_service`` → ``update_service`` → poll
  ``get_service`` until ``latest_ready_revision`` advances past the
  one we captured before the update and matches
  ``latest_created_revision``. The wrapper returns a dict with
  ``previous_value`` / ``new_value`` / ``revision_name`` /
  ``ready_at``.
* The timeout path: if the new revision never becomes ready inside
  ``poll_timeout_s``, the wrapper raises ``CloudRunUpdateError`` so
  the API endpoint can surface a 503.

The tests stub ``run_v2.ServicesAsyncClient`` and ``asyncio.sleep`` so
no GCP credentials, network, or wall-clock waits are involved.
``run_v2.Service`` / ``Container`` / ``EnvVar`` themselves are real
protos — the wrapper passes the Service to ``UpdateServiceRequest``
which is type-strict, so fakes don't suffice for the message values.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from google.cloud import run_v2

from app.services.cloud_run_client import (
    ALLOWED_ENV_VARS,
    CloudRunUpdateError,
    update_env_var,
)


def _service(
    *,
    ready_revision: str | None,
    created_revision: str | None,
    envs: list[tuple[str, str]],
) -> run_v2.Service:
    """Build a real ``run_v2.Service`` with the fields the wrapper reads.

    ``run_v2.UpdateServiceRequest`` is a strict proto and refuses to
    accept fakes, so the get_service mock has to return real Services.
    Only the fields the wrapper touches are populated; everything else
    keeps its proto default.
    """
    service = run_v2.Service()
    if ready_revision is not None:
        service.latest_ready_revision = ready_revision
    if created_revision is not None:
        service.latest_created_revision = created_revision
    container = run_v2.Container(
        env=[run_v2.EnvVar(name=n, value=v) for n, v in envs]
    )
    service.template.containers.append(container)
    return service


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    get_service_returns: list[run_v2.Service],
) -> MagicMock:
    """Install a fake ``ServicesAsyncClient`` whose ``get_service``
    returns the provided Services in order. Stub ``asyncio.sleep`` so
    the poll loop runs with zero wall-clock cost.

    Returns the ``fake_client`` MagicMock so individual tests can
    inspect how it was called.
    """
    fake_client = MagicMock()
    fake_client.get_service = AsyncMock(side_effect=list(get_service_returns))
    fake_client.update_service = AsyncMock(return_value=MagicMock())

    monkeypatch.setattr(
        "app.services.cloud_run_client.run_v2.ServicesAsyncClient",
        MagicMock(return_value=fake_client),
    )
    monkeypatch.setattr(
        "app.services.cloud_run_client.asyncio.sleep",
        AsyncMock(return_value=None),
    )
    return fake_client


# ─────────────────────────── allowlist guards ───────────────────────────


async def test_allowlist_rejects_other_var_name() -> None:
    """Any env var name not in ALLOWED_ENV_VARS raises before any RPC."""
    assert "SOMETHING_ELSE" not in ALLOWED_ENV_VARS
    with pytest.raises(ValueError, match="not in the allowlist"):
        await update_env_var(name="SOMETHING_ELSE", value="x")


async def test_allowlist_rejects_dangerous_var_names() -> None:
    """Defensive: even names a real attacker would target if they had
    the runtime — DATABASE_URL, BETTER_AUTH_SECRET, GEMINI_API_KEY —
    are rejected by the allowlist. The IAM grant alone would let any
    in-process code patch any env, so this check is the actual guard."""
    for var in ("DATABASE_URL", "BETTER_AUTH_SECRET", "GEMINI_API_KEY"):
        assert var not in ALLOWED_ENV_VARS
        with pytest.raises(ValueError):
            await update_env_var(name=var, value="x")


async def test_allowlist_message_names_the_module() -> None:
    """The error message tells whoever sees it which file to edit if
    they really need to broaden the allowlist (so it gets an
    auditable diff). Regression guard against drifting the message
    into something useless like 'permission denied'."""
    with pytest.raises(ValueError, match="cloud_run_client.py"):
        await update_env_var(name="SOMETHING_ELSE", value="x")


# ─────────────────────────── happy path ─────────────────────────────────


async def test_update_env_var_polls_until_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_service returns initial state with old-rev ready;
    update_service is called once; polling sees new-rev created but
    not ready on the first poll, then ready on the second poll. The
    wrapper returns the dict with previous_value, new_value,
    revision_name, ready_at populated."""
    initial = _service(
        ready_revision="projects/p/locations/r/services/fis-backend/revisions/old-rev",
        created_revision="projects/p/locations/r/services/fis-backend/revisions/old-rev",
        envs=[("LLM_USE_FILES_API", "false"), ("FOO", "bar")],
    )
    pending = _service(
        ready_revision="projects/p/locations/r/services/fis-backend/revisions/old-rev",
        created_revision="projects/p/locations/r/services/fis-backend/revisions/new-rev",
        envs=[("LLM_USE_FILES_API", "true"), ("FOO", "bar")],
    )
    ready = _service(
        ready_revision="projects/p/locations/r/services/fis-backend/revisions/new-rev",
        created_revision="projects/p/locations/r/services/fis-backend/revisions/new-rev",
        envs=[("LLM_USE_FILES_API", "true"), ("FOO", "bar")],
    )
    fake_client = _install_fake_client(
        monkeypatch, get_service_returns=[initial, pending, ready]
    )

    result = await update_env_var(
        name="LLM_USE_FILES_API",
        value="true",
        poll_interval_s=1,
        poll_timeout_s=120,
    )

    assert result["previous_value"] == "false"
    assert result["new_value"] == "true"
    assert result["revision_name"] == "new-rev"
    assert "ready_at" in result and result["ready_at"]
    assert fake_client.update_service.await_count == 1
    # initial fetch + 2 poll fetches
    assert fake_client.get_service.await_count == 3


async def test_update_env_var_appends_when_var_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If LLM_USE_FILES_API isn't on the service yet, the wrapper
    appends it. ``previous_value`` is ``None`` in that case — the
    endpoint surfaces this as ``previous_state=False`` (since
    ``None == "true"`` is ``False``)."""
    initial = _service(
        ready_revision="projects/p/locations/r/services/fis-backend/revisions/old-rev",
        created_revision="projects/p/locations/r/services/fis-backend/revisions/old-rev",
        envs=[("FOO", "bar")],
    )
    ready = _service(
        ready_revision="projects/p/locations/r/services/fis-backend/revisions/new-rev",
        created_revision="projects/p/locations/r/services/fis-backend/revisions/new-rev",
        envs=[("FOO", "bar"), ("LLM_USE_FILES_API", "true")],
    )
    _install_fake_client(monkeypatch, get_service_returns=[initial, ready])

    result = await update_env_var(name="LLM_USE_FILES_API", value="true")

    assert result["previous_value"] is None
    assert result["new_value"] == "true"
    assert result["revision_name"] == "new-rev"


# ─────────────────────────── timeout path ───────────────────────────────


async def test_update_env_var_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """If latest_ready_revision never advances past the original
    revision within poll_timeout_s, raise CloudRunUpdateError so the
    endpoint surfaces a 503 to the caller."""
    initial = _service(
        ready_revision="projects/p/locations/r/services/fis-backend/revisions/old-rev",
        created_revision="projects/p/locations/r/services/fis-backend/revisions/old-rev",
        envs=[("LLM_USE_FILES_API", "false")],
    )

    # Stuck on the old revision forever — the new revision was created
    # but never became ready (e.g. crashlooping container). Fresh
    # Service objects each iteration so the wrapper's mutation of the
    # template doesn't leak into the next "get".
    def _make_stuck() -> run_v2.Service:
        return _service(
            ready_revision="projects/p/locations/r/services/fis-backend/revisions/old-rev",
            created_revision="projects/p/locations/r/services/fis-backend/revisions/new-rev",
            envs=[("LLM_USE_FILES_API", "true")],
        )

    fake_client = MagicMock()
    fake_client.get_service = AsyncMock(
        side_effect=[initial] + [_make_stuck() for _ in range(100)]
    )
    fake_client.update_service = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        "app.services.cloud_run_client.run_v2.ServicesAsyncClient",
        MagicMock(return_value=fake_client),
    )
    monkeypatch.setattr(
        "app.services.cloud_run_client.asyncio.sleep",
        AsyncMock(return_value=None),
    )

    with pytest.raises(CloudRunUpdateError, match="did not become ready"):
        await update_env_var(
            name="LLM_USE_FILES_API",
            value="true",
            poll_interval_s=1,
            poll_timeout_s=2,
        )

    # Verify the loop actually polled, not just bailed on the first
    # iteration (bug-class: off-by-one / inverted condition).
    assert fake_client.get_service.await_count >= 2
