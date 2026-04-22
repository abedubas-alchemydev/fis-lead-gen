"""Tests for finra_pdf_service: 200 PDF, 404, non-PDF body, cache hit."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.finra_pdf_service import (
    FINRA_PDF_URL_TEMPLATE,
    FinraPdfFetchError,
    FinraPdfNotFound,
    fetch_and_cache_brokercheck_pdf,
    fetch_brokercheck_pdf,
)


@pytest.fixture
def tmp_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "pdf_cache_dir", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
@respx.mock
async def test_fetch_returns_bytes_on_200() -> None:
    url = FINRA_PDF_URL_TEMPLATE.format(crd=154975)
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"%PDF-1.7\n...",
            headers={"content-type": "application/pdf"},
        )
    )

    result = await fetch_brokercheck_pdf(154975)

    assert result.startswith(b"%PDF")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_raises_not_found_on_404() -> None:
    url = FINRA_PDF_URL_TEMPLATE.format(crd=9999999)
    respx.get(url).mock(return_value=httpx.Response(404))

    with pytest.raises(FinraPdfNotFound):
        await fetch_brokercheck_pdf(9999999)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_raises_on_non_pdf_body() -> None:
    url = FINRA_PDF_URL_TEMPLATE.format(crd=154975)
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"<html>Login required</html>",
            headers={"content-type": "text/html"},
        )
    )

    with pytest.raises(FinraPdfFetchError):
        await fetch_brokercheck_pdf(154975)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_and_cache_writes_pdf_to_disk(tmp_cache_dir: Path) -> None:
    url = FINRA_PDF_URL_TEMPLATE.format(crd=154975)
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"%PDF-1.7\nhello",
            headers={"content-type": "application/pdf"},
        )
    )

    cache_path = await fetch_and_cache_brokercheck_pdf(154975)

    assert cache_path == tmp_cache_dir / "finra" / "154975.pdf"
    assert cache_path.read_bytes().startswith(b"%PDF")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_and_cache_serves_from_cache(tmp_cache_dir: Path) -> None:
    cache_dir = tmp_cache_dir / "finra"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "154975.pdf"
    cache_path.write_bytes(b"%PDF-cached")

    result_path = await fetch_and_cache_brokercheck_pdf(154975)

    assert result_path == cache_path
    assert result_path.read_bytes() == b"%PDF-cached"
