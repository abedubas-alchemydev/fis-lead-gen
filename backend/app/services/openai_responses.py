from __future__ import annotations

import asyncio
import json
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings


class OpenAIConfigurationError(RuntimeError):
    pass


class OpenAIExtractionError(RuntimeError):
    pass


class OpenAIClearingExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clearing_partner: str | None = Field(default=None, max_length=255)
    clearing_type: Literal["fully_disclosed", "self_clearing", "omnibus", "unknown"]
    agreement_date: str | None = Field(default=None, description="ISO date in YYYY-MM-DD format when present.")
    confidence_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_excerpt: str | None = Field(default=None, max_length=1200)


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
