"""Tests for finra_pdf_service: 200 PDF, 404, non-PDF body.

The persistent disk cache was removed in Sprint 2 task #20 (the on-demand
``/brokercheck.pdf`` endpoint now serves bytes straight from the upstream
response). The only public function left is ``fetch_brokercheck_pdf``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.finra_pdf_service import (
    FINRA_PDF_URL_TEMPLATE,
    FinraPdfFetchError,
    FinraPdfNotFound,
    fetch_brokercheck_pdf,
)


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
