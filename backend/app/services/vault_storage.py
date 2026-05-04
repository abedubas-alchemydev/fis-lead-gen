"""GCS object-storage wrapper for the Vault file-upload feature.

Scope: thin async-friendly facade over ``google-cloud-storage`` so the
endpoint layer doesn't have to know about Bucket / Blob objects, and so
the per-process Client is lazy-initialized (avoids import-time ADC
lookups during test collection).

All operations target the bucket named in ``settings.vault_storage_bucket``
(env: ``VAULT_STORAGE_BUCKET``). When that setting is empty we raise
``VaultStorageError`` with a clear message — the endpoint layer maps
this to a 503 so the operator sees "this env isn't configured" rather
than an opaque KeyError.

Object naming: ``vault/{user_id}/{folder_id}/{uuid}.{ext}``. The
``user_id`` prefix is a defense-in-depth control — a leaked object name
is not guessable across tenants because the user id segment is the
BetterAuth user id, not anything iterable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import timedelta
from pathlib import PurePosixPath

from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError

from app.core.config import settings

logger = logging.getLogger(__name__)


class VaultStorageError(RuntimeError):
    """Raised when the GCS layer can't satisfy a caller's request."""


_DEFAULT_SIGNED_URL_TTL_SECONDS = 300

# Lazy-init module-level client so a missing GOOGLE_APPLICATION_CREDENTIALS
# at import time doesn't crash the test suite. Built on first call.
_client: storage.Client | None = None
_client_lock = asyncio.Lock()


def _bucket_name() -> str:
    """Resolve the env-configured bucket; surface a clear error on miss."""
    name = getattr(settings, "vault_storage_bucket", "") or os.environ.get(
        "VAULT_STORAGE_BUCKET", ""
    )
    if not name:
        raise VaultStorageError(
            "VAULT_STORAGE_BUCKET is not configured. Set it on the Cloud Run "
            "service or in backend/.env for local dev."
        )
    return name


async def _get_client() -> storage.Client:
    """Lazy single-instance client. Cheap to call repeatedly."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            # storage.Client() blocks during ADC resolution — wrap in an
            # executor so the event loop isn't held up on cold start.
            _client = await asyncio.to_thread(storage.Client)
    return _client


def _build_object_name(
    *, user_id: str, folder_id: int, original_filename: str
) -> str:
    """Compose ``vault/{user_id}/{folder_id}/{uuid}.{ext}``.

    Original filename is preserved on the DB row; only its extension is
    used here so the GCS console listing has the right MIME-by-suffix
    detection without exposing user-controlled paths in the object key.
    """
    suffix = PurePosixPath(original_filename).suffix.lower()
    # Clamp pathological extensions — the allowlist is enforced at the
    # endpoint layer, but defense-in-depth keeps weird ".exe.exe.exe"
    # things out of object names.
    if not suffix or len(suffix) > 16:
        suffix = ""
    return f"vault/{user_id}/{folder_id}/{uuid.uuid4()}{suffix}"


async def upload_file(
    *,
    user_id: str,
    folder_id: int,
    original_filename: str,
    content: bytes,
    content_type: str,
) -> str:
    """Upload ``content`` to GCS and return the object name."""
    client = await _get_client()
    bucket = client.bucket(_bucket_name())
    object_name = _build_object_name(
        user_id=user_id,
        folder_id=folder_id,
        original_filename=original_filename,
    )
    blob = bucket.blob(object_name)
    try:
        await asyncio.to_thread(
            blob.upload_from_string, content, content_type=content_type
        )
    except GoogleCloudError as exc:
        raise VaultStorageError(
            f"Failed to upload {original_filename!r} to GCS: {exc}"
        ) from exc
    return object_name


async def download_bytes(object_name: str) -> bytes:
    """Fetch object contents — used by the processing orchestrator if it
    needs to re-read bytes that aren't already buffered in memory."""
    client = await _get_client()
    bucket = client.bucket(_bucket_name())
    blob = bucket.blob(object_name)
    try:
        return await asyncio.to_thread(blob.download_as_bytes)
    except GoogleCloudError as exc:
        raise VaultStorageError(
            f"Failed to download {object_name!r} from GCS: {exc}"
        ) from exc


async def delete_object(object_name: str) -> None:
    """Hard-delete the object. Idempotent — silently swallows 404 because
    the DB row is the source of truth and a missing GCS blob just means
    the row's already orphaned and we're cleaning up."""
    client = await _get_client()
    bucket = client.bucket(_bucket_name())
    blob = bucket.blob(object_name)
    try:
        await asyncio.to_thread(blob.delete)
    except GoogleCloudError as exc:
        # 404 is fine; anything else surfaces.
        if "404" in str(exc) or "NotFound" in type(exc).__name__:
            logger.info(
                "Vault GCS delete: object %s already gone", object_name
            )
            return
        raise VaultStorageError(
            f"Failed to delete {object_name!r} from GCS: {exc}"
        ) from exc


async def signed_download_url(
    object_name: str, *, ttl_seconds: int = _DEFAULT_SIGNED_URL_TTL_SECONDS
) -> str:
    """Mint a V4 signed URL the FE can 302 to.

    ADC on Cloud Run gives us a service-account identity but the SA
    doesn't carry a private key, so ``blob.generate_signed_url`` needs
    an explicit IAM signer (``service_account_email`` + ``access_token``)
    to delegate signing back to the IAM Credentials API. This is the
    documented Cloud Run signed-URL pattern.
    """
    client = await _get_client()
    bucket = client.bucket(_bucket_name())
    blob = bucket.blob(object_name)

    credentials, _project = google_auth_default()
    # Refresh once to populate the access token used for IAM-delegated
    # signing. ``refresh`` is sync and fast; wrap in to_thread to be tidy.
    await asyncio.to_thread(credentials.refresh, GoogleAuthRequest())

    sa_email = getattr(credentials, "service_account_email", None)
    access_token = getattr(credentials, "token", None)

    def _sign() -> str:
        kwargs: dict[str, object] = {
            "version": "v4",
            "expiration": timedelta(seconds=ttl_seconds),
            "method": "GET",
        }
        # On Cloud Run the credentials lack a local key; delegate signing
        # to IAM. Locally with a JSON key file these args are not needed
        # and would actually break the signing flow.
        if sa_email and access_token:
            kwargs["service_account_email"] = sa_email
            kwargs["access_token"] = access_token
        return blob.generate_signed_url(**kwargs)  # type: ignore[arg-type]

    try:
        return await asyncio.to_thread(_sign)
    except GoogleCloudError as exc:
        raise VaultStorageError(
            f"Failed to mint signed URL for {object_name!r}: {exc}"
        ) from exc
