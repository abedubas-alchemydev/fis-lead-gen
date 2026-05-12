"""Unit tests for the GCS storage facade.

Focus is the exception-mapping in ``signed_download_url``: on Cloud Run the
IAM-delegated signer raises ``google.auth.exceptions.TransportError`` (a
``GoogleAuthError`` subclass) when ``iam.serviceAccounts.signBlob`` is
denied. The original except clause caught only ``GoogleCloudError`` and
let auth failures escape as bare 500s. These tests pin the wider catch.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError, TransportError
from google.cloud.exceptions import GoogleCloudError

from app.services import vault_storage
from app.services.vault_storage import VaultStorageError, signed_download_url


def _install_signing_chain(
    monkeypatch: pytest.MonkeyPatch, *, signer_exc: BaseException
) -> None:
    """Wire up the minimum mock graph so ``signed_download_url`` can run.

    ``signer_exc`` is the exception ``blob.generate_signed_url`` will raise
    when the test calls into ``_sign``.
    """
    monkeypatch.setenv("VAULT_STORAGE_BUCKET", "test-bucket")

    fake_blob = MagicMock()
    fake_blob.generate_signed_url.side_effect = signer_exc

    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob

    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket

    fake_credentials = MagicMock()
    fake_credentials.service_account_email = "fake@example.iam.gserviceaccount.com"
    fake_credentials.token = "fake-token"
    fake_credentials.refresh = MagicMock()

    async def _fake_get_client() -> MagicMock:
        return fake_client

    monkeypatch.setattr(vault_storage, "_get_client", _fake_get_client)
    monkeypatch.setattr(
        vault_storage,
        "google_auth_default",
        lambda: (fake_credentials, "test-project"),
    )


async def test_signed_download_url_wraps_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403 from IAM signBlob raises TransportError (a GoogleAuthError);
    must surface as VaultStorageError so the endpoint returns 503."""
    _install_signing_chain(monkeypatch, signer_exc=TransportError("denied"))

    with pytest.raises(VaultStorageError):
        await signed_download_url("vault/u/1/test.pdf")


async def test_signed_download_url_wraps_refresh_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale ADC credentials raise RefreshError — also a GoogleAuthError;
    same wrapping path."""
    _install_signing_chain(monkeypatch, signer_exc=RefreshError("stale"))

    with pytest.raises(VaultStorageError):
        await signed_download_url("vault/u/1/test.pdf")


async def test_signed_download_url_wraps_google_cloud_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-existing wrapping behavior must still hold for GoogleCloudError."""
    _install_signing_chain(monkeypatch, signer_exc=GoogleCloudError("backend"))

    with pytest.raises(VaultStorageError):
        await signed_download_url("vault/u/1/test.pdf")
