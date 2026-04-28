from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

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


class GeminiClassificationExtraction(BaseModel):
    """Text-only clearing classification (no PDF input).

    Backs ``services/clearing_classifier.py`` -- a single canonical
    classifier that consumes the FINRA ``firm_operations_text`` plus the
    FOCUS report text and returns one of the four Deshorn-canonical
    labels. Distinct from ``GeminiClearingExtraction`` (which extracts
    a partner + type from a PDF) because the classifier does not need
    a partner field, takes plain text rather than an inline PDF, and
    uses a different prompt/schema.
    """
    model_config = ConfigDict(extra="forbid")

    classification: Literal["fully_disclosed", "self_clearing", "omnibus", "unknown"]
    confidence_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)


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

        response_payload = await self._dispatch_pdf_extract(
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
        response_text = self._extract_response_text(response_payload)

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise GeminiExtractionError("Gemini returned invalid JSON for clearing extraction.") from exc

        return GeminiClearingExtraction.model_validate(self._normalize_text_fields(parsed))

    async def extract_classification_data(self, *, prompt: str) -> GeminiClassificationExtraction:
        """Run a text-only Gemini call that returns the canonical clearing label.

        Used by ``services/clearing_classifier.py``. The prompt embeds
        Deshorn's three definitions verbatim and the FINRA + FOCUS source
        texts; the response is constrained to the four-value enum via a
        JSON schema. No PDF / Files API path -- the prompt is short
        enough to live entirely in inline text, and going through the
        Files API would just add latency.
        """
        if not settings.gemini_api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

        schema = {
            "type": "OBJECT",
            "properties": {
                "classification": {
                    "type": "STRING",
                    "enum": ["fully_disclosed", "self_clearing", "omnibus", "unknown"],
                },
                "confidence_score": {"type": "NUMBER"},
                "rationale": {"type": "STRING"},
            },
            "required": ["classification", "confidence_score", "rationale"],
            "propertyOrdering": ["classification", "confidence_score", "rationale"],
        }

        payload: dict[str, object] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "temperature": 0.1,
                "topP": 0.95,
            },
        }
        response_payload = await self._post_with_retries(payload)
        response_text = self._extract_response_text(response_payload)

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise GeminiExtractionError("Gemini returned invalid JSON for clearing classification.") from exc

        return GeminiClassificationExtraction.model_validate(self._normalize_text_fields(parsed))

    async def extract_financial_data(self, *, pdf_bytes_base64: str, prompt: str) -> GeminiFinancialExtraction:
        if not settings.gemini_api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

        response_payload = await self._dispatch_pdf_extract(
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
            response_payload = await self._post_with_retries(payload)
        else:
            # Fallback: raw PDF — dispatch routes between inline base64 and
            # the Files API based on size, keeping container memory flat for
            # the 20-45 MB FOCUS filings that previously OOM-killed the pod.
            assert pdf_bytes_base64 is not None  # invariant from the guard above
            response_payload = await self._dispatch_pdf_extract(
                pdf_bytes_base64=pdf_bytes_base64,
                prompt=prompt,
                schema=schema,
            )

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

        response_payload = await self._dispatch_pdf_extract(
            pdf_bytes_base64=pdf_bytes_base64,
            prompt=prompt,
            schema=schema,
        )
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

    def _build_files_api_payload(
        self,
        *,
        file_uri: str,
        prompt: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        """Build a Gemini API payload that references a previously-uploaded file.

        Used for PDFs above ``gemini_files_api_threshold_mb``. The PDF bytes
        are NOT in this payload — the model fetches them from ``file_uri``.
        """
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "file_data": {
                                "mime_type": "application/pdf",
                                "file_uri": file_uri,
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

    @staticmethod
    def _pdf_byte_size_from_b64(pdf_bytes_base64: str) -> int:
        """Return the raw byte size of a base64 string without decoding it.

        Avoids materializing the decoded bytes just to measure them — saves
        ~45 MB of transient memory on the hot path. Standard base64 strings
        are always a multiple of 4 chars; padding ``=`` accounts for the
        final 1-2 byte trim.
        """
        if not pdf_bytes_base64:
            return 0
        return (len(pdf_bytes_base64) * 3) // 4 - pdf_bytes_base64.count("=")

    async def _dispatch_pdf_extract(
        self,
        *,
        pdf_bytes_base64: str,
        prompt: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        """Route a PDF Gemini call through inline base64 or the Files API.

        - size <= ``gemini_files_api_threshold_mb`` → inline base64 (existing
          fast path, fewer round-trips).
        - threshold < size <= ``gemini_inline_pdf_max_size_mb`` → upload to
          Files API, reference by file_uri, delete after the model call.
          Keeps container memory flat regardless of PDF size.
        - size > ``gemini_inline_pdf_max_size_mb`` → reject (defense-in-depth;
          the downloader normally caps before bytes ever reach this client).
        """
        threshold_bytes = settings.gemini_files_api_threshold_mb * 1024 * 1024
        max_bytes = settings.gemini_inline_pdf_max_size_mb * 1024 * 1024
        pdf_size_bytes = self._pdf_byte_size_from_b64(pdf_bytes_base64)

        if pdf_size_bytes > max_bytes:
            raise GeminiExtractionError(
                f"PDF size {pdf_size_bytes} bytes exceeds "
                f"gemini_inline_pdf_max_size_mb={settings.gemini_inline_pdf_max_size_mb} MB."
            )

        if pdf_size_bytes <= threshold_bytes:
            payload = self._build_payload(
                pdf_bytes_base64=pdf_bytes_base64, prompt=prompt, schema=schema
            )
            return await self._post_with_retries(payload)

        # Files API path: decode -> upload -> reference -> generate -> delete.
        pdf_bytes = base64.b64decode(pdf_bytes_base64)
        file_name, file_uri = await self._upload_pdf_to_files_api(pdf_bytes)
        try:
            payload = self._build_files_api_payload(
                file_uri=file_uri, prompt=prompt, schema=schema
            )
            return await self._post_with_retries(payload)
        finally:
            await self._delete_files_api_file(file_name)

    def _files_api_upload_url(self) -> str:
        """Compose the Gemini Files API multipart upload endpoint.

        The upload host inserts ``/upload`` before the API version segment of
        ``gemini_api_base`` (e.g. ``…/v1beta`` → ``…/upload/v1beta``).
        """
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}/upload{parsed.path}/files?uploadType=multipart"

    def _files_api_resource_url(self, file_name: str) -> str:
        """Compose the Gemini Files API resource URL for ``file_name``.

        ``file_name`` is the ``files/<id>`` reference returned by the upload
        response (NOT the full URI).
        """
        return f"{self.base_url}/{file_name}"

    async def _upload_pdf_to_files_api(self, pdf_bytes: bytes) -> tuple[str, str]:
        """Upload PDF bytes via multipart/related and return ``(name, uri)``.

        Returns the ``files/<id>`` resource name and the absolute file_uri
        that the generateContent payload references. Polls until the file
        becomes ``ACTIVE`` if the upload response reports ``PROCESSING``.
        """
        boundary = f"----GeminiUpload{secrets.token_hex(16)}"
        metadata = json.dumps(
            {"file": {"display_name": "focus_filing.pdf"}}
        ).encode("utf-8")
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("ascii"),
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                metadata,
                f"\r\n--{boundary}\r\n".encode("ascii"),
                b"Content-Type: application/pdf\r\n\r\n",
                pdf_bytes,
                f"\r\n--{boundary}--\r\n".encode("ascii"),
            ]
        )
        headers = {
            "x-goog-api-key": settings.gemini_api_key,
            "Content-Type": f"multipart/related; boundary={boundary}",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self._files_api_upload_url(), headers=headers, content=body
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            raise GeminiExtractionError(
                f"Files API upload failed with status {exc.response.status_code}: "
                f"{detail or 'No response body.'}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GeminiExtractionError(
                "Files API upload failed due to a network error."
            ) from exc

        payload = response.json()
        file_obj = payload.get("file") if isinstance(payload, dict) else None
        if not isinstance(file_obj, dict):
            raise GeminiExtractionError("Files API upload response missing 'file' object.")
        file_name = file_obj.get("name")
        file_uri = file_obj.get("uri")
        if not isinstance(file_name, str) or not isinstance(file_uri, str):
            raise GeminiExtractionError(
                "Files API upload response missing 'name' or 'uri'."
            )
        if file_obj.get("state") != "ACTIVE":
            await self._poll_files_api_until_active(file_name)
        return file_name, file_uri

    async def _poll_files_api_until_active(
        self, file_name: str, *, attempts: int = 6, delay_seconds: float = 2.0
    ) -> None:
        """Poll a Files API resource until state == ACTIVE or attempts run out."""
        url = self._files_api_resource_url(file_name)
        headers = {"x-goog-api-key": settings.gemini_api_key}
        for _ in range(attempts):
            await asyncio.sleep(delay_seconds)
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(url, headers=headers)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise GeminiExtractionError(
                    f"Files API status poll failed for {file_name}."
                ) from exc
            payload = response.json()
            state = payload.get("state") if isinstance(payload, dict) else None
            if state == "ACTIVE":
                return
            if state == "FAILED":
                raise GeminiExtractionError(
                    f"Files API processing failed for {file_name}."
                )
        raise GeminiExtractionError(
            f"Files API resource {file_name} did not reach ACTIVE state after polling."
        )

    async def _delete_files_api_file(self, file_name: str) -> None:
        """Best-effort delete to keep the Files API quota tidy.

        Failures are logged, not raised — orphaned files TTL out on Google's
        side and the response we already got is the one the user needs.
        """
        url = self._files_api_resource_url(file_name)
        headers = {"x-goog-api-key": settings.gemini_api_key}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.delete(url, headers=headers)
            if response.status_code >= 400:
                logger.warning(
                    "Files API delete failed for %s: status=%s body=%s",
                    file_name,
                    response.status_code,
                    response.text[:200],
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "Files API delete network error for %s: %s", file_name, exc
            )

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
