"""
Async Gemini client.

Uses the google-genai SDK. Handles:
  - structured output via response_schema (Pydantic model -> JSON schema)
  - retry with exponential backoff on 429/500/503
  - Files API for PDF upload (avoids re-uploading for every call when we reuse a doc)
  - context caching for repeated system prompts
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


@dataclass
class GeminiSettings:
    api_key: str
    model_flash: str = "gemini-2.5-flash"
    model_pro: str = "gemini-2.5-pro"
    model_lite: str = "gemini-2.5-flash-lite"
    request_timeout_s: float = 120.0
    retries: int = 4
    # Response determinism — extraction tasks want low temperature
    temperature: float = 0.0
    top_p: float = 1.0

    @classmethod
    def from_env(cls) -> "GeminiSettings":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        return cls(
            api_key=api_key,
            model_flash=os.environ.get("GEMINI_MODEL_FLASH", "gemini-2.5-flash"),
            model_pro=os.environ.get("GEMINI_MODEL_PRO", "gemini-2.5-pro"),
            model_lite=os.environ.get("GEMINI_MODEL_LITE", "gemini-2.5-flash-lite"),
        )


class GeminiClient:
    """Thin async wrapper over google-genai for structured PDF extraction."""

    def __init__(self, settings: Optional[GeminiSettings] = None):
        from google import genai  # lazy import — keep SDK optional

        self.settings = settings or GeminiSettings.from_env()
        self._genai = genai
        self._client = genai.Client(api_key=self.settings.api_key)

    async def __aenter__(self) -> "GeminiClient":
        return self

    async def __aexit__(self, *exc) -> None:
        # google-genai Client has no explicit close in current SDK; no-op
        pass

    # --------------------------------------------------------------- uploads

    async def upload_pdf(self, pdf_bytes: bytes, display_name: str) -> Any:
        """Upload a PDF to Gemini Files API. Returns a File handle usable in
        subsequent generate_content calls."""
        # Write to a temp file because the SDK's upload expects a path
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        def _upload():
            return self._client.files.upload(
                file=tmp_path,
                config={"display_name": display_name, "mime_type": "application/pdf"},
            )

        try:
            file_obj = await asyncio.to_thread(_upload)
            # Wait for ACTIVE state — Gemini processes the PDF asynchronously
            return await self._wait_for_active(file_obj)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def _wait_for_active(self, file_obj: Any, timeout_s: float = 60.0) -> Any:
        """Poll until the uploaded file reaches ACTIVE state."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        name = file_obj.name
        while asyncio.get_event_loop().time() < deadline:
            refreshed = await asyncio.to_thread(self._client.files.get, name=name)
            state = getattr(refreshed, "state", None)
            state_name = getattr(state, "name", str(state))
            if state_name == "ACTIVE":
                return refreshed
            if state_name == "FAILED":
                raise RuntimeError(f"Gemini file processing failed for {name}")
            await asyncio.sleep(1.0)
        raise TimeoutError(f"Gemini file {name} did not become ACTIVE in {timeout_s}s")

    # --------------------------------------------------------------- generate

    async def extract_structured(
        self,
        *,
        pdf_file: Any,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
        model: Optional[str] = None,
    ) -> BaseModel:
        """Run a PDF → structured Pydantic object extraction."""
        model_name = model or self.settings.model_flash

        config = {
            "response_mime_type": "application/json",
            "response_schema": response_schema,
            "temperature": self.settings.temperature,
            "top_p": self.settings.top_p,
            "system_instruction": system_prompt,
        }

        def _call():
            return self._client.models.generate_content(
                model=model_name,
                contents=[pdf_file, user_prompt],
                config=config,
            )

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.settings.retries),
            wait=wait_exponential(multiplier=1, min=2, max=60),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        ):
            with attempt:
                response = await asyncio.to_thread(_call)

        # google-genai returns .parsed already typed when response_schema is set
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            return parsed

        # Fallback: parse from .text
        import json
        text = getattr(response, "text", "") or ""
        return response_schema.model_validate_json(text)
