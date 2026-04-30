"""Cloud Vision OCR pre-pass for scanned-image X-17A-5 PDFs.

The clearing-extraction pipeline drops scanned-image filings into the
``pdf_unparseable`` Unknown-reasons bucket because pdfplumber surfaces
no extractable text and Gemini cannot read pixel-only layouts as
reliably as text. This module wraps Google Cloud Vision's
``documentTextDetection`` (via ``batch_annotate_files``) so
``pdf_processor.py`` can route those filings through OCR before the
LLM call.

Cost discipline:

* The caller is responsible for the < 50 char gating — Vision is NOT
  invoked on every PDF, only on the ~5% that pdfplumber cannot read.
* A same-process SHA-256 cache prevents redundant Vision calls inside
  a single Fresh Regen, since the downloader fans out the same filing
  to multiple consumers (clearing, FOCUS CEO, financial multi-year).

Authentication uses the runtime's default GCP credentials (Cloud Run
service account on prod, local ADC for dev). The client-construction
path validates that the SDK can resolve credentials and surfaces a
typed configuration error instead of an opaque RuntimeError so the
``provider_error`` extraction-status path stays observable.
"""

from __future__ import annotations

import logging
from hashlib import sha256

logger = logging.getLogger(__name__)


class VisionOcrConfigurationError(RuntimeError):
    """Raised when the Vision SDK cannot be initialized.

    Distinct from :class:`VisionOcrError` so callers can decide whether
    to surface a transient ``provider_error`` (extraction failure) or a
    more durable mis-configuration that should fail the whole regen.
    """


class VisionOcrError(RuntimeError):
    """Raised when a Vision OCR call fails for a specific PDF.

    Wraps both API-level failures (5xx, quota) and parse failures so
    ``pdf_processor.py`` can collapse the catch into a single branch
    that maps to ``extraction_status='provider_error'`` with a
    ``vision_ocr_failed`` note.
    """


class VisionOCR:
    """Thin wrapper around ``ImageAnnotatorClient`` with a SHA-256 cache.

    The client is constructed lazily on first use so ``pdf_processor``
    instantiation does not pay the SDK auth round-trip during app
    startup (e.g. when the OCR path is never exercised on a regen
    that has zero scanned-image filings).
    """

    def __init__(self) -> None:
        self._client = None  # lazy
        self._cache: dict[str, str] = {}

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google.cloud import vision_v1
        except ImportError as exc:  # pragma: no cover - import guard
            raise VisionOcrConfigurationError(
                "google-cloud-vision is not installed. Install "
                "google-cloud-vision==3.7.4 (see backend/requirements.txt)."
            ) from exc
        try:
            self._client = vision_v1.ImageAnnotatorClient()
        except Exception as exc:
            raise VisionOcrConfigurationError(
                f"Cloud Vision client initialization failed: {exc}"
            ) from exc
        return self._client

    def ocr_pdf(self, pdf_bytes: bytes) -> str:
        """Return the OCR text for a PDF. Cached by SHA-256 of the bytes.

        The cache is local to this ``VisionOCR`` instance — by design,
        since ``pdf_processor`` constructs one per service lifetime.
        Cross-process caching belongs at the storage layer (the
        downloader-tempdir contract is per-extraction, so a new
        process never sees the same bytes twice anyway).
        """
        if not pdf_bytes:
            return ""

        key = sha256(pdf_bytes).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        from google.cloud import vision_v1

        client = self._get_client()
        request = vision_v1.AnnotateFileRequest(
            input_config=vision_v1.InputConfig(
                content=pdf_bytes,
                mime_type="application/pdf",
            ),
            features=[
                vision_v1.Feature(
                    type_=vision_v1.Feature.Type.DOCUMENT_TEXT_DETECTION
                )
            ],
        )

        try:
            response = client.batch_annotate_files(requests=[request])
        except Exception as exc:
            raise VisionOcrError(f"Vision batch_annotate_files failed: {exc}") from exc

        text = self._extract_text(response)
        self._cache[key] = text
        return text

    @staticmethod
    def _extract_text(response: object) -> str:
        """Concatenate per-page ``full_text_annotation.text`` from a
        ``BatchAnnotateFilesResponse``.

        Vision's PDF response shape nests the per-page annotations
        inside ``responses[0].responses`` (the outer list is per-file,
        the inner list is per-page). Defensive coding avoids assuming
        every page surfaces text — pages that are entirely blank still
        appear as response entries with an empty annotation.
        """
        outer = getattr(response, "responses", None)
        if not outer:
            return ""

        chunks: list[str] = []
        for file_response in outer:
            inner = getattr(file_response, "responses", None) or []
            for page_response in inner:
                annotation = getattr(page_response, "full_text_annotation", None)
                page_text = getattr(annotation, "text", "") if annotation is not None else ""
                if page_text:
                    chunks.append(page_text)
        return "\n\n".join(chunks)
