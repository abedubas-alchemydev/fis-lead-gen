"""
Thin wrappers that glue the Gemini client to the Pydantic schemas.

Why separate modules: keeps the deterministic parsers and LLM parsers behind
the same interface (bytes in, FirmProfile/FocusReport out) so the cross-validator
can treat them as interchangeable data sources.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..schema.models import FirmProfile, FocusReport
from .gemini_client import GeminiClient
from .prompts import (
    FINRA_SYSTEM_PROMPT,
    FINRA_USER_PROMPT,
    FOCUS_SYSTEM_PROMPT,
    FOCUS_USER_PROMPT,
)

logger = logging.getLogger(__name__)


async def extract_finra_with_llm(
    pdf_bytes: bytes,
    client: GeminiClient,
    *,
    crd_hint: Optional[str] = None,
    use_pro: bool = False,
) -> FirmProfile:
    """LLM-based extraction of a FINRA BrokerCheck PDF.

    Returns a FirmProfile populated directly from the LLM's structured output.
    Separate from the deterministic parser so they can cross-validate.
    """
    display_name = f"finra_{crd_hint or 'unknown'}"
    file_obj = await client.upload_pdf(pdf_bytes, display_name=display_name)
    model = client.settings.model_pro if use_pro else client.settings.model_flash

    user_prompt = FINRA_USER_PROMPT
    if crd_hint:
        user_prompt = (
            f"Expected CRD number: {crd_hint}. If the PDF's CRD doesn't match, "
            f"note the mismatch in parse_warnings.\n\n" + user_prompt
        )

    profile: FirmProfile = await client.extract_structured(
        pdf_file=file_obj,
        system_prompt=FINRA_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_schema=FirmProfile,
        model=model,
    )
    return profile


async def extract_focus_with_llm(
    pdf_bytes: bytes,
    client: GeminiClient,
    *,
    use_pro: bool = False,
) -> FocusReport:
    """LLM-based extraction of an SEC X-17A-5 PDF."""
    file_obj = await client.upload_pdf(pdf_bytes, display_name="focus_pdf")
    model = client.settings.model_pro if use_pro else client.settings.model_flash

    report: FocusReport = await client.extract_structured(
        pdf_file=file_obj,
        system_prompt=FOCUS_SYSTEM_PROMPT,
        user_prompt=FOCUS_USER_PROMPT,
        response_schema=FocusReport,
        model=model,
    )
    return report
