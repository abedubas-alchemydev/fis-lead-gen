from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings

_GEMINI_KEY_SHAPE = re.compile(r"^AIzaSy[A-Za-z0-9_\-]{33}$")


class GeminiConfigurationError(RuntimeError):
    pass


class GeminiExtractionError(RuntimeError):
    pass


class GeminiClearingExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clearing_partner: str | None = Field(default=None, max_length=255)
    clearing_type: Literal["fully_disclosed", "self_clearing", "omnibus", "unknown"]
    agreement_date: str | None = Field(default=None, description="ISO date in YYYY-MM-DD format when present.")
    confidence_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_excerpt: str | None = Field(default=None, max_length=1200)


class GeminiFinancialExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_date: str | None = Field(default=None, description="ISO date in YYYY-MM-DD format when present.")
    net_capital: float | None = None
    excess_net_capital: float | None = None
    total_assets: float | None = None
    required_min_capital: float | None = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_excerpt: str | None = Field(default=None, max_length=1200)


class GeminiFocusCeoExtraction(BaseModel):
    """Structured extraction of CEO contact info + net capital from a FOCUS Report PDF."""
    model_config = ConfigDict(extra="forbid")

    ceo_name: str | None = Field(default=None, max_length=255)
    ceo_title: str | None = Field(default=None, max_length=255)
    ceo_phone: str | None = Field(default=None, max_length=64)
    ceo_email: str | None = Field(default=None, max_length=320)
    net_capital: float | None = None
    report_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    confidence_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_excerpt: str | None = Field(default=None, max_length=1200)


class GeminiResponsesClient:
    def __init__(self) -> None:
        self.base_url = settings.gemini_api_base.rstrip("/")
        self.timeout = settings.gemini_request_timeout_seconds
        self.max_retries = max(1, settings.gemini_request_max_retries)

        # Fail fast on corrupted key values. Google API keys are 39 chars
        # matching ^AIzaSy[A-Za-z0-9_-]{33}$. Past incident: a shell-quoting
        # error stored the key as `-n "AIzaSy...\r\n"` — httpx rejected the
        # header as LocalProtocolError, surfacing as an opaque "network error".
        # An empty key is permitted here — the per-call check at the top of
        # each extract_* method still raises GeminiConfigurationError.
        if settings.gemini_api_key and not _GEMINI_KEY_SHAPE.match(settings.gemini_api_key):
            raise GeminiConfigurationError(
                f"GEMINI_API_KEY has invalid shape (length={len(settings.gemini_api_key)}). "
                f"Expected 39 chars matching ^AIzaSy[A-Za-z0-9_-]{{33}}$."
            )

    async def extract_clearing_data(self, *, pdf_bytes_base64: str, prompt: str) -> GeminiClearingExtraction:
        if not settings.gemini_api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

        payload = self._build_payload(
            pdf_bytes_base64=pdf_bytes_base64,
            prompt=prompt,
            schema={
                "type": "OBJECT",
                "properties": {
                    "clearing_partner": {"type": ["STRING", "NULL"]},
                    "clearing_type": {
                        "type": "STRING",
                        "enum": ["fully_disclosed", "self_clearing", "omnibus", "unknown"],
                    },
                    "agreement_date": {"type": ["STRING", "NULL"]},
                    "confidence_score": {"type": "NUMBER"},
                    "rationale": {"type": "STRING"},
                    "evidence_excerpt": {"type": ["STRING", "NULL"]},
                },
                "required": ["clearing_type", "confidence_score", "rationale"],
                "propertyOrdering": [
                    "clearing_partner",
                    "clearing_type",
                    "agreement_date",
                    "confidence_score",
                    "rationale",
                    "evidence_excerpt",
                ],
            },
        )
        response_payload = await self._post_with_retries(payload)
        response_text = self._extract_response_text(response_payload)

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise GeminiExtractionError("Gemini returned invalid JSON for clearing extraction.") from exc

        return GeminiClearingExtraction.model_validate(self._normalize_text_fields(parsed))

    async def extract_financial_data(self, *, pdf_bytes_base64: str, prompt: str) -> GeminiFinancialExtraction:
        if not settings.gemini_api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

        payload = self._build_payload(
            pdf_bytes_base64=pdf_bytes_base64,
            prompt=prompt,
            schema={
                "type": "OBJECT",
                "properties": {
                    "report_date": {"type": ["STRING", "NULL"]},
                    "net_capital": {"type": ["NUMBER", "NULL"]},
                    "excess_net_capital": {"type": ["NUMBER", "NULL"]},
                    "total_assets": {"type": ["NUMBER", "NULL"]},
                    "required_min_capital": {"type": ["NUMBER", "NULL"]},
                    "confidence_score": {"type": "NUMBER"},
                    "rationale": {"type": "STRING"},
                    "evidence_excerpt": {"type": ["STRING", "NULL"]},
                },
                "required": ["confidence_score", "rationale"],
                "propertyOrdering": [
                    "report_date",
                    "net_capital",
                    "excess_net_capital",
                    "total_assets",
                    "required_min_capital",
                    "confidence_score",
                    "rationale",
                    "evidence_excerpt",
                ],
            },
        )
        response_payload = await self._post_with_retries(payload)
        response_text = self._extract_response_text(response_payload)

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise GeminiExtractionError("Gemini returned invalid JSON for financial extraction.") from exc

        return GeminiFinancialExtraction.model_validate(self._normalize_text_fields(parsed))

    async def extract_focus_ceo_data(
        self,
        *,
        prompt: str,
        pdf_bytes_base64: str | None = None,
        page_images: list[dict[str, str]] | None = None,
    ) -> GeminiFocusCeoExtraction:
        """Extract CEO contact info and net capital from a FOCUS Report.

        Supports two modes:
        - **Vision mode** (preferred): pass ``page_images`` — list of rendered PNG
          page images. Uses the model's vision capabilities to read the document.
        - **PDF mode** (fallback): pass ``pdf_bytes_base64`` — raw PDF sent inline.

        Vision mode is preferred because it gives the model explicit visual layout
        information (headers, tables, signatures) that inline PDF parsing may miss.
        """
        if not settings.gemini_api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")
        if not page_images and not pdf_bytes_base64:
            raise GeminiExtractionError("Either page_images or pdf_bytes_base64 must be provided.")

        schema = {
            "type": "OBJECT",
            "properties": {
                "ceo_name": {"type": ["STRING", "NULL"]},
                "ceo_title": {"type": ["STRING", "NULL"]},
                "ceo_phone": {"type": ["STRING", "NULL"]},
                "ceo_email": {"type": ["STRING", "NULL"]},
                "net_capital": {"type": ["NUMBER", "NULL"]},
                "report_date": {"type": ["STRING", "NULL"]},
                "confidence_score": {"type": "NUMBER"},
                "rationale": {"type": "STRING"},
                "evidence_excerpt": {"type": ["STRING", "NULL"]},
            },
            "required": ["confidence_score", "rationale"],
            "propertyOrdering": [
                "ceo_name", "ceo_title", "ceo_phone", "ceo_email",
                "net_capital", "report_date",
                "confidence_score", "rationale", "evidence_excerpt",
            ],
        }

        # Vision mode (preferred): send rendered page images
        if page_images:
            payload = self._build_vision_payload(
                page_images=page_images,
                prompt=prompt,
                schema=schema,
            )
        else:
            # Fallback: send raw PDF inline
            payload = self._build_payload(
                pdf_bytes_base64=pdf_bytes_base64,
                prompt=prompt,
                schema=schema,
            )

        response_payload = await self._post_with_retries(payload)
        response_text = self._extract_response_text(response_payload)

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise GeminiExtractionError("Gemini returned invalid JSON for FOCUS CEO extraction.") from exc

        return GeminiFocusCeoExtraction.model_validate(self._normalize_text_fields(parsed))

    async def extract_multi_year_financial_data(
        self, *, pdf_bytes_base64: str, prompt: str,
    ) -> list[GeminiFinancialExtraction]:
        """Extract financial data for BOTH current and prior year from the same PDF.

        Most X-17A-5 annual audits contain comparative figures for two years
        on the same balance sheet.  This method asks Gemini to return an array.
        """
        if not settings.gemini_api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

        schema = {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "report_date": {"type": ["STRING", "NULL"]},
                    "net_capital": {"type": ["NUMBER", "NULL"]},
                    "excess_net_capital": {"type": ["NUMBER", "NULL"]},
                    "total_assets": {"type": ["NUMBER", "NULL"]},
                    "required_min_capital": {"type": ["NUMBER", "NULL"]},
                    "confidence_score": {"type": "NUMBER"},
                    "rationale": {"type": "STRING"},
                    "evidence_excerpt": {"type": ["STRING", "NULL"]},
                },
                "required": ["confidence_score", "rationale"],
            },
        }

        payload = self._build_payload(pdf_bytes_base64=pdf_bytes_base64, prompt=prompt, schema=schema)
        response_payload = await self._post_with_retries(payload)
        response_text = self._extract_response_text(response_payload)

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise GeminiExtractionError("Gemini returned invalid JSON for multi-year extraction.") from exc

        if not isinstance(parsed, list):
            parsed = [parsed]

        results: list[GeminiFinancialExtraction] = []
        for item in parsed:
            if isinstance(item, dict):
                results.append(GeminiFinancialExtraction.model_validate(self._normalize_text_fields(item)))
        return results

    def _build_payload(self, *, pdf_bytes_base64: str, prompt: str, schema: dict[str, object]) -> dict[str, object]:

        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "application/pdf",
                                "data": pdf_bytes_base64,
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "temperature": 0.1,
                "topP": 0.95,
            },
        }

    def _build_vision_payload(
        self,
        *,
        page_images: list[dict[str, str]],
        prompt: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        """Build a Gemini API payload that sends page images (vision mode).

        Each item in page_images must have {"mime_type": "image/png", "data": "<base64>"}.
        This sends images instead of a raw PDF, using the model's vision capabilities.
        """
        parts: list[dict[str, object]] = [{"text": prompt}]
        for img in page_images:
            parts.append({
                "inline_data": {
                    "mime_type": img["mime_type"],
                    "data": img["data"],
                }
            })

        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "temperature": 0.1,
                "topP": 0.95,
            },
        }

    async def _post_with_retries(self, payload: dict[str, object]) -> dict[str, object]:
        url = f"{self.base_url}/models/{settings.gemini_pdf_model}:generateContent"
        headers = {"x-goog-api-key": settings.gemini_api_key}
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in {408, 409, 429, 500, 502, 503, 504} or attempt == self.max_retries:
                    detail = exc.response.text.strip()
                    raise GeminiExtractionError(
                        f"Gemini request failed with status {exc.response.status_code}: {detail or 'No response body.'}"
                    ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self.max_retries:
                    raise GeminiExtractionError("Gemini request failed due to a network error.") from exc

            await asyncio.sleep(min(2**attempt, 8))

        raise GeminiExtractionError("Gemini request failed after retries.") from last_error

    def _extract_response_text(self, payload: dict[str, object]) -> str:
        candidates = payload.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            raise GeminiExtractionError("Gemini response did not include any candidates.")

        first = candidates[0]
        if not isinstance(first, dict):
            raise GeminiExtractionError("Gemini response candidate was malformed.")

        content = first.get("content", {})
        if not isinstance(content, dict):
            raise GeminiExtractionError("Gemini response content was malformed.")

        parts = content.get("parts", [])
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text

        raise GeminiExtractionError("Gemini response did not include structured text output.")

    def _normalize_text_fields(self, payload: object) -> object:
        if not isinstance(payload, dict):
            return payload

        normalized = dict(payload)
        for field_name, max_length in {
            "rationale": 1000,
            "evidence_excerpt": 1200,
            "clearing_partner": 255,
            "ceo_name": 255,
            "ceo_title": 255,
            "ceo_phone": 64,
            "ceo_email": 320,
        }.items():
            value = normalized.get(field_name)
            if isinstance(value, str):
                compact = " ".join(value.split())
                normalized[field_name] = compact[:max_length]
        return normalized
