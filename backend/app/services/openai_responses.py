from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings

logger = logging.getLogger(__name__)


class OpenAIConfigurationError(RuntimeError):
    pass


class OpenAIExtractionError(RuntimeError):
    pass


# ─────────────────────── Files API LRU (ADR-0001 phase 2) ───────────────────
#
# Mirrors the Gemini Files API LRU pattern (see ``gemini_responses``). Cache
# maps ``accession_number → (file_id, uploaded_at)``. OpenAI's Files API does
# not document a server-side TTL the way Gemini does, so the 23h horizon here
# is a self-imposed cap to keep the cache bounded; orphaned files leak per
# OpenAI's storage policy and TTL out via the same mechanism a manual delete
# would use.
_FILE_ID_CACHE: "OrderedDict[str, tuple[str, datetime]]" = OrderedDict()
_FILE_ID_CACHE_LOCK = asyncio.Lock()
_FILE_ID_TTL = timedelta(hours=23)
_FILE_ID_CACHE_MAX_ENTRIES = 256


def _file_id_cache_key(accession_number: str) -> str:
    return accession_number.replace("-", "")


async def _file_id_cache_get(accession_number: str) -> str | None:
    key = _file_id_cache_key(accession_number)
    async with _FILE_ID_CACHE_LOCK:
        hit = _FILE_ID_CACHE.get(key)
        if hit is None:
            return None
        file_id, uploaded_at = hit
        if datetime.now(timezone.utc) - uploaded_at >= _FILE_ID_TTL:
            _FILE_ID_CACHE.pop(key, None)
            return None
        _FILE_ID_CACHE.move_to_end(key)
        return file_id


async def _file_id_cache_put(accession_number: str, file_id: str) -> None:
    key = _file_id_cache_key(accession_number)
    async with _FILE_ID_CACHE_LOCK:
        _FILE_ID_CACHE[key] = (file_id, datetime.now(timezone.utc))
        _FILE_ID_CACHE.move_to_end(key)
        while len(_FILE_ID_CACHE) > _FILE_ID_CACHE_MAX_ENTRIES:
            _FILE_ID_CACHE.popitem(last=False)


async def _file_id_cache_evict(accession_number: str) -> None:
    key = _file_id_cache_key(accession_number)
    async with _FILE_ID_CACHE_LOCK:
        _FILE_ID_CACHE.pop(key, None)


def _file_id_cache_clear_for_tests() -> None:
    _FILE_ID_CACHE.clear()


class OpenAIClearingExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clearing_partner: str | None = Field(default=None, max_length=255)
    clearing_type: Literal["fully_disclosed", "self_clearing", "omnibus", "unknown"]
    agreement_date: str | None = Field(default=None, description="ISO date in YYYY-MM-DD format when present.")
    confidence_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_excerpt: str | None = Field(default=None, max_length=1200)


class OpenAIClassificationExtraction(BaseModel):
    """Text-only clearing classification, mirrors GeminiClassificationExtraction."""
    model_config = ConfigDict(extra="forbid")

    classification: Literal["fully_disclosed", "self_clearing", "omnibus", "unknown"]
    confidence_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)


class OpenAIResponsesClient:
    def __init__(self) -> None:
        self.base_url = settings.openai_api_base.rstrip("/")
        self.timeout = settings.openai_request_timeout_seconds
        self.max_retries = max(1, settings.openai_request_max_retries)

    async def extract_clearing_data(self, *, pdf_bytes_base64: str, filename: str, prompt: str) -> OpenAIClearingExtraction:
        if not settings.openai_api_key:
            raise OpenAIConfigurationError("OPENAI_API_KEY is not configured.")

        payload = self._build_payload(pdf_bytes_base64=pdf_bytes_base64, filename=filename, prompt=prompt)
        response_payload = await self._post_with_retries(payload)
        response_text = self._extract_response_text(response_payload)

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise OpenAIExtractionError("OpenAI returned invalid JSON for clearing extraction.") from exc

        return OpenAIClearingExtraction.model_validate(self._normalize_text_fields(parsed))

    async def extract_clearing_data_from_path(
        self,
        *,
        local_path: Path,
        accession_number: str,
        filename: str,
        prompt: str,
    ) -> OpenAIClearingExtraction:
        """Files-API-default clearing extraction (ADR-0001 phase 2).

        Uploads the PDF via OpenAI's Files API (``POST /v1/files`` with
        ``purpose="user_data"``) or reuses an LRU-cached ``file_id`` for the
        same ``accession_number``, then references it in the
        ``responses.create`` call via ``input_file.file_id`` instead of
        inlining the bytes. On a 4xx response that smells like a stale /
        deleted file, the cache entry is evicted and the upload + call are
        retried exactly once.
        """
        if not settings.openai_api_key:
            raise OpenAIConfigurationError("OPENAI_API_KEY is not configured.")

        response_payload = await self._call_pdf_via_files_api(
            local_path=local_path,
            accession_number=accession_number,
            filename=filename,
            prompt=prompt,
        )
        response_text = self._extract_response_text(response_payload)
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise OpenAIExtractionError(
                "OpenAI returned invalid JSON for clearing extraction."
            ) from exc

        return OpenAIClearingExtraction.model_validate(
            self._normalize_text_fields(parsed)
        )

    async def _call_pdf_via_files_api(
        self,
        *,
        local_path: Path,
        accession_number: str,
        filename: str,
        prompt: str,
    ) -> dict[str, object]:
        """LRU-cached upload + ``responses.create`` referencing the file_id."""
        file_id = await self._upload_or_reuse_file(
            local_path=local_path,
            accession_number=accession_number,
            filename=filename,
        )
        payload = self._build_files_api_payload(
            file_id=file_id, prompt=prompt
        )
        try:
            return await self._post_with_retries(payload)
        except OpenAIExtractionError as exc:
            if not self._looks_like_stale_file_id(exc):
                raise
            logger.info(
                "OpenAI Files API file_id %s appears stale (accession=%s); "
                "evicting and retrying upload + responses.create once.",
                file_id, accession_number,
            )
            await _file_id_cache_evict(accession_number)
            new_file_id = await self._upload_pdf_to_files_api(
                local_path=local_path, filename=filename
            )
            await _file_id_cache_put(accession_number, new_file_id)
            payload = self._build_files_api_payload(
                file_id=new_file_id, prompt=prompt
            )
            return await self._post_with_retries(payload)

    @staticmethod
    def _looks_like_stale_file_id(exc: OpenAIExtractionError) -> bool:
        """Heuristic for the expired/deleted file_id failure mode.

        OpenAI returns 404 with ``{"error": {"code": "file_not_found", ...}}``
        or 400 with ``invalid_request_error`` referencing the file. Match on
        the wrapped error string.
        """
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "status 404",
                "file_not_found",
                "no such file",
                "file not found",
            )
        )

    async def _upload_or_reuse_file(
        self,
        *,
        local_path: Path,
        accession_number: str,
        filename: str,
    ) -> str:
        """LRU-aware Files API upload. Returns the OpenAI ``file_id``."""
        hit = await _file_id_cache_get(accession_number)
        if hit is not None:
            return hit
        file_id = await self._upload_pdf_to_files_api(
            local_path=local_path, filename=filename
        )
        await _file_id_cache_put(accession_number, file_id)
        return file_id

    async def _upload_pdf_to_files_api(
        self, *, local_path: Path, filename: str
    ) -> str:
        """POST /v1/files with multipart body referencing the local PDF.

        Reads the PDF off the local tempfile in a worker thread (off-loop
        so a 50 MB read doesn't block other coroutines), then sends the
        bytes as the ``file`` field of a multipart form alongside
        ``purpose="user_data"`` per OpenAI's Files API contract.
        """
        pdf_bytes = await asyncio.to_thread(local_path.read_bytes)

        url = f"{self.base_url}/files"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        files = {
            "file": (filename, pdf_bytes, "application/pdf"),
            "purpose": (None, "user_data"),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, files=files)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            raise OpenAIExtractionError(
                f"OpenAI Files API upload failed with status "
                f"{exc.response.status_code}: {detail or 'No response body.'}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OpenAIExtractionError(
                "OpenAI Files API upload failed due to a network error."
            ) from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise OpenAIExtractionError("OpenAI Files API response was malformed.")
        file_id = payload.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise OpenAIExtractionError(
                "OpenAI Files API response missing 'id' field."
            )
        return file_id

    def _build_files_api_payload(
        self, *, file_id: str, prompt: str
    ) -> dict[str, object]:
        """Build a ``responses.create`` payload that references an uploaded file."""
        schema = OpenAIClearingExtraction.model_json_schema()
        return {
            "model": settings.openai_pdf_model,
            "store": False,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_file", "file_id": file_id},
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "clearing_extraction",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": 600,
        }

    async def extract_classification_data(self, *, prompt: str) -> OpenAIClassificationExtraction:
        """Run a text-only OpenAI call that returns the canonical clearing label.

        Mirrors GeminiResponsesClient.extract_classification_data. Used by
        services/clearing_classifier.py when settings.llm_provider == 'openai'.
        """
        if not settings.openai_api_key:
            raise OpenAIConfigurationError("OPENAI_API_KEY is not configured.")

        schema = OpenAIClassificationExtraction.model_json_schema()
        payload = {
            "model": settings.openai_pdf_model,
            "store": False,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "clearing_classification",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": 400,
        }
        response_payload = await self._post_with_retries(payload)
        response_text = self._extract_response_text(response_payload)

        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise OpenAIExtractionError("OpenAI returned invalid JSON for clearing classification.") from exc

        return OpenAIClassificationExtraction.model_validate(self._normalize_text_fields(parsed))

    def _build_payload(self, *, pdf_bytes_base64: str, filename: str, prompt: str) -> dict[str, object]:
        schema = OpenAIClearingExtraction.model_json_schema()
        return {
            "model": settings.openai_pdf_model,
            "store": False,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_file",
                            "filename": filename,
                            "file_data": f"data:application/pdf;base64,{pdf_bytes_base64}",
                        },
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "clearing_extraction",
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": 600,
        }

    async def _post_with_retries(self, payload: dict[str, object]) -> dict[str, object]:
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/responses"
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
                    raise OpenAIExtractionError(
                        f"OpenAI request failed with status {exc.response.status_code}: {detail or 'No response body.'}"
                    ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self.max_retries:
                    raise OpenAIExtractionError("OpenAI request failed due to a network error.") from exc

            await asyncio.sleep(min(2**attempt, 8))

        raise OpenAIExtractionError("OpenAI request failed after retries.") from last_error

    def _extract_response_text(self, payload: dict[str, object]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        for output_item in payload.get("output", []):
            if not isinstance(output_item, dict):
                continue
            for content in output_item.get("content", []):
                if not isinstance(content, dict):
                    continue
                if content.get("type") in {"output_text", "text"}:
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        return text

        raise OpenAIExtractionError("OpenAI response did not include structured text output.")

    def _normalize_text_fields(self, payload: object) -> object:
        if not isinstance(payload, dict):
            return payload

        normalized = dict(payload)
        for field_name, max_length in {"rationale": 1000, "evidence_excerpt": 1200, "clearing_partner": 255}.items():
            value = normalized.get(field_name)
            if isinstance(value, str):
                compact = " ".join(value.split())
                normalized[field_name] = compact[:max_length]
        return normalized
