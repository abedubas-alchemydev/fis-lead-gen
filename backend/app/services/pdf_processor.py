from __future__ import annotations

from app.services.llm_parser import LlmParserService
from app.services.service_models import ClearingExtractionResult, DownloadedPdfRecord


class PdfProcessorService:
    def __init__(self) -> None:
        self.llm_parser = LlmParserService()

    async def process_downloaded_pdf(self, pdf_record: DownloadedPdfRecord) -> ClearingExtractionResult:
        return await self.llm_parser.extract_structured_data(pdf_record)
