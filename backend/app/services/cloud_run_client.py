"""Cloud Run service-config helper, intentionally narrow.

This module wraps :mod:`google.cloud.run_v2` for the single purpose of
flipping a small set of feature-flag env vars on the ``fis-backend``
service itself. It is **not** a general-purpose Cloud Run client.

Security model
--------------
``roles/run.developer`` granted to the runtime service account
(``136029935063-compute@developer.gserviceaccount.com``) on
``fis-backend`` is what allows this code to mutate the live service.
That IAM grant alone would let any code on the runtime patch any env
var. :data:`ALLOWED_ENV_VARS` is the application-layer guard that
keeps the blast radius small — :func:`update_env_var` raises
``ValueError`` for any name not in the set, **before** any RPC is
issued. Adding a new flag means editing this list, which is a
reviewable diff.

The current single-entry allowlist exists so the
``POST /api/v1/pipeline/set-files-api-flag`` endpoint can let an admin
toggle ``LLM_USE_FILES_API`` from the Fresh Regen UI in-flow, without
needing a manual ``gcloud run services update``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from google.api_core import exceptions as gapi_exceptions
from google.cloud import run_v2

logger = logging.getLogger(__name__)


ALLOWED_ENV_VARS: frozenset[str] = frozenset({"LLM_USE_FILES_API"})
"""Env var names this wrapper is permitted to mutate on ``fis-backend``.

Anything else raises :class:`ValueError` from :func:`update_env_var`
before the wrapper makes a Cloud Run RPC. To allow a new variable,
add it here in a reviewable commit — do not parameterise this set
from configuration.
"""

SERVICE_NAME: str = (
    "projects/fis-lead-gen/locations/us-central1/services/fis-backend"
)
"""Fully-qualified Cloud Run v2 service name. Hardcoded on purpose:
this wrapper exists to mutate exactly this one service."""


class CloudRunUpdateError(RuntimeError):
    """Cloud Run API call failed, or the new revision did not become
    ready inside the poll window. Surfaced as a 503 by the endpoint."""


async def update_env_var(
    *,
    name: str,
    value: str,
    poll_interval_s: float = 5.0,
    poll_timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Patch ``fis-backend``'s env vars to set ``name=value`` and wait
    for the rollout to be ready.

    Args:
        name: Env var to update. **Must be in :data:`ALLOWED_ENV_VARS`.**
        value: New value. Stored as-is on the Cloud Run env entry.
        poll_interval_s: Seconds between ``get_service`` polls.
        poll_timeout_s: Total seconds to wait for the new revision to
            become ready before raising :class:`CloudRunUpdateError`.

    Returns:
        Dict with keys:

        * ``previous_value`` (``str | None``) — the value before the
          update, or ``None`` if the var did not previously exist.
        * ``new_value`` (``str``) — same as the ``value`` argument.
        * ``revision_name`` (``str``) — short revision name of the new
          ready revision (e.g. ``fis-backend-00042-abc``).
        * ``ready_at`` (``str``) — UTC ISO-8601 timestamp of when the
          poll loop observed readiness.

    Raises:
        ValueError: ``name`` is not in :data:`ALLOWED_ENV_VARS`.
        CloudRunUpdateError: The Cloud Run API rejected the update,
            or the new revision did not become ready within
            ``poll_timeout_s``.
    """
    if name not in ALLOWED_ENV_VARS:
        raise ValueError(
            f"Env var {name!r} is not in the allowlist "
            f"{sorted(ALLOWED_ENV_VARS)!r}. This wrapper is intentionally "
            "narrow — to allow a new variable, edit ALLOWED_ENV_VARS in "
            "app/services/cloud_run_client.py (reviewable diff)."
        )

    client = run_v2.ServicesAsyncClient()

    try:
        service = await client.get_service(name=SERVICE_NAME)
    except gapi_exceptions.GoogleAPICallError as exc:
        raise CloudRunUpdateError(
            f"Cloud Run get_service failed: {exc}"
        ) from exc

    original_ready_revision = service.latest_ready_revision

    previous_value: str | None = None
    new_envs: list[run_v2.EnvVar] = []
    found = False
    for env in service.template.containers[0].env:
        if env.name == name:
            previous_value = env.value
            new_envs.append(run_v2.EnvVar(name=name, value=value))
            found = True
        else:
            new_envs.append(env)
    if not found:
        new_envs.append(run_v2.EnvVar(name=name, value=value))

    # Mutate the env list on the service template in place. Cloud Run's
    # update_service request takes the full Service shape; replacing
    # the container env list is the only field this wrapper changes.
    container = service.template.containers[0]
    del container.env[:]
    container.env.extend(new_envs)

    try:
        await client.update_service(
            request=run_v2.UpdateServiceRequest(service=service)
        )
    except gapi_exceptions.GoogleAPICallError as exc:
        raise CloudRunUpdateError(
            f"Cloud Run update_service failed: {exc}"
        ) from exc

    # The LRO returned by update_service finishes when the config is
    # accepted; revision readiness is reported separately on the
    # Service.latest_ready_revision field. Poll that until the new
    # revision is ready, or give up after poll_timeout_s.
    elapsed = 0.0
    last_ready: str | None = original_ready_revision
    last_created: str | None = None
    while elapsed < poll_timeout_s:
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s
        try:
            svc = await client.get_service(name=SERVICE_NAME)
        except gapi_exceptions.GoogleAPICallError as exc:
            raise CloudRunUpdateError(
                f"Cloud Run get_service polling failed: {exc}"
            ) from exc
        last_ready = svc.latest_ready_revision
        last_created = svc.latest_created_revision
        if (
            last_ready
            and last_ready != original_ready_revision
            and last_ready == last_created
        ):
            return {
                "previous_value": previous_value,
                "new_value": value,
                "revision_name": last_ready.split("/")[-1],
                "ready_at": datetime.now(timezone.utc).isoformat(),
            }

    raise CloudRunUpdateError(
        f"Cloud Run revision did not become ready within {poll_timeout_s}s "
        f"(last_ready={last_ready!r}, last_created={last_created!r})"
    )
