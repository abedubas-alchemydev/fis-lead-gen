"""FOCUS Report CEO contact + net capital extraction.

Downloads the latest X-17A-5 PDF for a broker-dealer and uses Gemini to extract
the CEO's name, title, phone, email, and the firm's net capital figure.

Supports both:
- **On-demand** (single firm via the detail page button)
- **Batch** (all ~3000 firms via ``python -m scripts.run_focus_ceo_extraction``)

The extracted CEO contact is persisted as an ExecutiveContact with
source="focus_report" so it coexists with Apollo-sourced contacts.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.broker_dealer import BrokerDealer
from app.models.executive_contact import ExecutiveContact
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiResponsesClient,
)
from app.services.pdf_downloader import PdfDownloaderService
from app.services.pdf_text_extractor import extract_from_pdf

logger = logging.getLogger(__name__)

_FOCUS_CEO_PROMPT = """\
Read this broker-dealer annual audit PDF (SEC Form X-17A-5 / Statement of Financial Condition) \
and extract the following information:

1. **CEO or principal executive officer's full name** — typically found on the cover page, \
   in the header, or in the oath/affirmation section at the end where a senior officer signs.
2. **Their title** (e.g., "Chief Executive Officer", "President", "Managing Member").
3. **Their phone number** — if listed on the cover page, header, or contact section.
4. **Their email address** — if listed anywhere in the document.
5. **The firm's net capital** — from the "Computation of Net Capital" or \
   "Statement of Financial Condition" section. This is the regulatory net capital figure.
6. **The report date** — the fiscal year-end date the report covers (as YYYY-MM-DD).

IMPORTANT:
- Return ALL dollar values in FULL US DOLLARS (not thousands, not millions).
- If the document shows "$1,234" it means $1,234 not $1,234,000.
- If a value states "(in thousands)" then multiply by 1000.
- Use null for any field you cannot find with reasonable confidence.
- The confidence_score should reflect how certain you are about the CEO identification \
  and net capital extraction (0.0 = no useful data found, 1.0 = highly confident).
- Provide a brief rationale explaining where you found each piece of data.
"""


@dataclass(slots=True)
class FocusCeoExtractionResult:
    bd_id: int
    ceo_name: str | None
    ceo_title: str | None
    ceo_phone: str | None
    ceo_email: str | None
    net_capital: float | None
    report_date: date | None
    source_pdf_url: str | None
    confidence_score: float
    extraction_status: str  # "success" | "low_confidence" | "no_pdf" | "error"
    extraction_notes: str | None


_MAX_VISION_PAYLOAD_BYTES = 4 * 1024 * 1024  # 4MB max total image payload for Gemini


def _render_pdf_pages_to_images(
    pdf_base64: str,
    local_path: str | None = None,
    dpi: int = 150,
) -> list[dict[str, str]]:
    """Render selected PDF pages to PNG images for vision-model extraction.

    Selects pages 1-2 (facing page with contact info) + pages 3-8 (financial
    statements + net capital) + last 3 (supplemental schedules), converts each
    to a JPEG image, and returns them as base64-encoded dicts for the Gemini
    vision API.

    Handles scanned/image-based PDFs that pdfplumber can't read.
    Keeps total payload under 4MB to stay within Gemini limits.

    Uses pypdfium2 (pip-installable PDF renderer). No system deps needed.
    """
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    logging.getLogger("pypdfium2").setLevel(logging.ERROR)

    try:
        import pypdfium2 as pdfium

        if local_path and Path(local_path).exists():
            pdf = pdfium.PdfDocument(local_path)
        else:
            pdf_bytes = base64.b64decode(pdf_base64)
            pdf = pdfium.PdfDocument(pdf_bytes)

        total_pages = len(pdf)

        # Select relevant pages
        if total_pages <= 12:
            page_indices = list(range(total_pages))
        else:
            page_indices = list(range(min(8, total_pages)))
            last_start = max(8, total_pages - 3)
            page_indices.extend(range(last_start, total_pages))
            page_indices = sorted(set(page_indices))

        images: list[dict[str, str]] = []
        total_bytes = 0

        for page_idx in page_indices:
            page = pdf[page_idx]
            bitmap = page.render(scale=dpi / 72)
            pil_image = bitmap.to_pil()

            # Use JPEG instead of PNG — 3-5x smaller for scanned documents
            buf = io.BytesIO()
            if pil_image.mode == "RGBA":
                pil_image = pil_image.convert("RGB")
            pil_image.save(buf, format="JPEG", quality=80)
            img_bytes = buf.getvalue()

            # Check if adding this image would exceed the payload limit
            if total_bytes + len(img_bytes) > _MAX_VISION_PAYLOAD_BYTES and len(images) >= 3:
                # Already have enough pages — stop adding more
                logger.debug("Vision payload limit reached at page %d (%dKB total)", page_idx + 1, total_bytes // 1024)
                break

            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            images.append({
                "mime_type": "image/jpeg",
                "data": img_b64,
            })
            total_bytes += len(img_bytes)

        pdf.close()

        logger.debug(
            "PDF rendered: %d pages -> %d images (%dKB total)",
            total_pages, len(images), total_bytes // 1024,
        )

        return images

    except Exception as exc:
        logger.warning("PDF-to-image rendering failed: %s", exc)
        return []


class FocusCeoExtractionService:
    def __init__(self) -> None:
        self.downloader = PdfDownloaderService()
        self.gemini = GeminiResponsesClient()

    async def extract(
        self,
        db: AsyncSession,
        broker_dealer: BrokerDealer,
    ) -> FocusCeoExtractionResult:
        """Download the latest X-17A-5 PDF and extract CEO contact + net capital.

        Uses a two-tier strategy:
          1. pdfplumber text extraction (FREE, ~500ms) — works for ~70% of PDFs
          2. Gemini vision (API cost, ~15-30s) — fallback for scanned/non-standard PDFs

        The CEO contact is persisted in the executive_contacts table with
        source="focus_report" so it survives Apollo enrichment cycles.
        """

        # ── Step 1: Download the PDF ──
        try:
            pdf_record = await self.downloader.download_latest_x17a5_pdf(broker_dealer)
        except Exception as exc:
            logger.warning("PDF download failed for BD %d: %s", broker_dealer.id, exc)
            return FocusCeoExtractionResult(
                bd_id=broker_dealer.id,
                ceo_name=None, ceo_title=None, ceo_phone=None, ceo_email=None,
                net_capital=None, report_date=None,
                source_pdf_url=None,
                confidence_score=0.0,
                extraction_status="error",
                extraction_notes=f"PDF download failed: {exc}",
            )

        if pdf_record is None:
            return FocusCeoExtractionResult(
                bd_id=broker_dealer.id,
                ceo_name=None, ceo_title=None, ceo_phone=None, ceo_email=None,
                net_capital=None, report_date=None,
                source_pdf_url=None,
                confidence_score=0.0,
                extraction_status="no_pdf",
                extraction_notes="No X-17A-5 PDF available for this broker-dealer.",
            )

        # ── Step 2: Try pdfplumber first (FREE, ~500ms) ──
        if pdf_record.local_document_path:
            text_result = await asyncio.to_thread(extract_from_pdf, pdf_record.local_document_path)
            if text_result.success:
                confidence = 0.95 if (text_result.contact_name and text_result.net_capital) else 0.80
                # Persist if we found a contact name
                if text_result.contact_name and confidence >= 0.5:
                    await self._upsert_focus_contact(
                        db,
                        broker_dealer=broker_dealer,
                        ceo_name=text_result.contact_name,
                        ceo_title=text_result.contact_title or "Filing Contact",
                        ceo_email=text_result.contact_email,
                        ceo_phone=text_result.contact_phone,
                    )
                return FocusCeoExtractionResult(
                    bd_id=broker_dealer.id,
                    ceo_name=text_result.contact_name,
                    ceo_title=text_result.contact_title,
                    ceo_phone=text_result.contact_phone,
                    ceo_email=text_result.contact_email,
                    net_capital=text_result.net_capital,
                    report_date=None,
                    source_pdf_url=pdf_record.source_pdf_url,
                    confidence_score=confidence,
                    extraction_status="success",
                    extraction_notes="Extracted via text analysis (no API cost).",
                )

        # ── Step 3: Fallback to Gemini vision ──
        page_images = await asyncio.to_thread(
            _render_pdf_pages_to_images,
            pdf_record.bytes_base64,
            pdf_record.local_document_path,
        )

        try:
            if page_images:
                extraction = await self.gemini.extract_focus_ceo_data(
                    page_images=page_images,
                    prompt=_FOCUS_CEO_PROMPT,
                )
            else:
                extraction = await self.gemini.extract_focus_ceo_data(
                    pdf_bytes_base64=pdf_record.bytes_base64,
                    prompt=_FOCUS_CEO_PROMPT,
                )
        except GeminiConfigurationError as exc:
            return FocusCeoExtractionResult(
                bd_id=broker_dealer.id,
                ceo_name=None, ceo_title=None, ceo_phone=None, ceo_email=None,
                net_capital=None, report_date=None,
                source_pdf_url=pdf_record.source_pdf_url,
                confidence_score=0.0,
                extraction_status="error",
                extraction_notes=str(exc),
            )
        except GeminiExtractionError as exc:
            logger.warning("Gemini vision extraction failed for BD %d: %s", broker_dealer.id, exc)
            return FocusCeoExtractionResult(
                bd_id=broker_dealer.id,
                ceo_name=None, ceo_title=None, ceo_phone=None, ceo_email=None,
                net_capital=None, report_date=None,
                source_pdf_url=pdf_record.source_pdf_url,
                confidence_score=0.0,
                extraction_status="error",
                extraction_notes=f"Gemini vision extraction failed: {exc}",
            )

        report_date: date | None = None
        if extraction.report_date:
            try:
                report_date = date.fromisoformat(extraction.report_date)
            except ValueError:
                pass

        status = "success" if extraction.confidence_score >= 0.5 else "low_confidence"

        if extraction.ceo_name and extraction.confidence_score >= 0.5:
            await self._upsert_focus_contact(
                db,
                broker_dealer=broker_dealer,
                ceo_name=extraction.ceo_name,
                ceo_title=extraction.ceo_title or "Principal Executive Officer",
                ceo_email=extraction.ceo_email,
                ceo_phone=extraction.ceo_phone,
            )

        return FocusCeoExtractionResult(
            bd_id=broker_dealer.id,
            ceo_name=extraction.ceo_name,
            ceo_title=extraction.ceo_title,
            ceo_phone=extraction.ceo_phone,
            ceo_email=extraction.ceo_email,
            net_capital=extraction.net_capital,
            report_date=report_date,
            source_pdf_url=pdf_record.source_pdf_url,
            confidence_score=extraction.confidence_score,
            extraction_status=status,
            extraction_notes=f"Extracted via Gemini vision. {extraction.rationale}",
        )

    async def _extract_without_db(
        self,
        bd_id: int,
        bd_filings_index_url: str | None,
        bd_cik: str | None,
    ) -> FocusCeoExtractionResult:
        """Download PDF -> try pdfplumber (free & fast) -> fall back to Gemini vision.

        No database connection is held open during this method.
        """
        fake_bd = BrokerDealer()
        fake_bd.id = bd_id
        fake_bd.filings_index_url = bd_filings_index_url
        fake_bd.cik = bd_cik

        # Step 1: Download PDF
        try:
            pdf_record = await self.downloader.download_latest_x17a5_pdf(fake_bd)
        except Exception as exc:
            logger.warning("PDF download failed for BD %d: %s", bd_id, exc)
            return FocusCeoExtractionResult(
                bd_id=bd_id,
                ceo_name=None, ceo_title=None, ceo_phone=None, ceo_email=None,
                net_capital=None, report_date=None, source_pdf_url=None,
                confidence_score=0.0, extraction_status="error",
                extraction_notes=f"PDF download failed: {exc}",
            )

        if pdf_record is None:
            return FocusCeoExtractionResult(
                bd_id=bd_id,
                ceo_name=None, ceo_title=None, ceo_phone=None, ceo_email=None,
                net_capital=None, report_date=None, source_pdf_url=None,
                confidence_score=0.0, extraction_status="no_pdf",
                extraction_notes="No X-17A-5 PDF available for this broker-dealer.",
            )

        # Step 2: Try pdfplumber first (FREE, ~500ms, no API call)
        if pdf_record.local_document_path:
            text_result = await asyncio.to_thread(extract_from_pdf, pdf_record.local_document_path)
            if text_result.success:
                # Got data from text extraction — no Gemini needed
                confidence = 0.95 if (text_result.contact_name and text_result.net_capital) else 0.80
                return FocusCeoExtractionResult(
                    bd_id=bd_id,
                    ceo_name=text_result.contact_name,
                    ceo_title=text_result.contact_title,
                    ceo_phone=text_result.contact_phone,
                    ceo_email=text_result.contact_email,
                    net_capital=text_result.net_capital,
                    report_date=None,
                    source_pdf_url=pdf_record.source_pdf_url,
                    confidence_score=confidence,
                    extraction_status="success",
                    extraction_notes="Extracted via pdfplumber (text mode, no API cost).",
                )

        # Step 3: pdfplumber failed — fall back to Gemini vision
        page_images = await asyncio.to_thread(
            _render_pdf_pages_to_images,
            pdf_record.bytes_base64,
            pdf_record.local_document_path,
        )

        try:
            if page_images:
                extraction = await self.gemini.extract_focus_ceo_data(
                    page_images=page_images,
                    prompt=_FOCUS_CEO_PROMPT,
                )
            else:
                extraction = await self.gemini.extract_focus_ceo_data(
                    pdf_bytes_base64=pdf_record.bytes_base64,
                    prompt=_FOCUS_CEO_PROMPT,
                )
        except (GeminiConfigurationError, GeminiExtractionError) as exc:
            logger.warning("Gemini vision extraction failed for BD %d: %s", bd_id, exc)
            return FocusCeoExtractionResult(
                bd_id=bd_id,
                ceo_name=None, ceo_title=None, ceo_phone=None, ceo_email=None,
                net_capital=None, report_date=None,
                source_pdf_url=pdf_record.source_pdf_url,
                confidence_score=0.0, extraction_status="error",
                extraction_notes=f"Gemini vision fallback failed: {exc}",
            )

        report_date: date | None = None
        if extraction.report_date:
            try:
                report_date = date.fromisoformat(extraction.report_date)
            except ValueError:
                pass

        return FocusCeoExtractionResult(
            bd_id=bd_id,
            ceo_name=extraction.ceo_name,
            ceo_title=extraction.ceo_title,
            ceo_phone=extraction.ceo_phone,
            ceo_email=extraction.ceo_email,
            net_capital=extraction.net_capital,
            report_date=report_date,
            source_pdf_url=pdf_record.source_pdf_url,
            confidence_score=extraction.confidence_score,
            extraction_status="success" if extraction.confidence_score >= 0.5 else "low_confidence",
            extraction_notes=f"Extracted via Gemini vision (fallback). {extraction.rationale}",
        )

    async def run_batch(
        self,
        db: AsyncSession,
        *,
        offset: int = 0,
        limit: int | None = None,
        skip_existing: bool = True,
    ) -> dict[str, int]:
        """Run FOCUS CEO extraction for all broker-dealers that have SEC filings.

        Architecture: DB is only open for brief reads/writes. The slow Gemini call
        runs with NO database connection, preventing Neon timeout kills.

        Args:
            db: Database session (used only for the initial query).
            offset: Skip the first N eligible firms (for resuming).
            limit: Max number of firms to process (None = all).
            skip_existing: If True, skip firms that already have a focus_report contact.

        Returns:
            Summary dict with counts for total, success, no_pdf, error, skipped.
        """
        from app.db.session import SessionLocal

        # ── Quick read: get the list of firms to process ──
        stmt = (
            select(BrokerDealer.id, BrokerDealer.name, BrokerDealer.filings_index_url, BrokerDealer.cik)
            .where(BrokerDealer.filings_index_url.is_not(None))
            .order_by(BrokerDealer.id.asc())
        )
        all_bd_rows = list((await db.execute(stmt)).all())

        if skip_existing:
            existing_bd_ids_stmt = (
                select(ExecutiveContact.bd_id)
                .where(ExecutiveContact.source == "focus_report")
                .distinct()
            )
            existing_bd_ids = set((await db.execute(existing_bd_ids_stmt)).scalars().all())
            skipped_count = sum(1 for row in all_bd_rows if row[0] in existing_bd_ids)
            all_bd_rows = [row for row in all_bd_rows if row[0] not in existing_bd_ids]
            if skipped_count:
                print(f"  Skipping {skipped_count} firms already extracted (use --force to redo).")

        if offset > 0:
            all_bd_rows = all_bd_rows[offset:]
        if limit is not None:
            all_bd_rows = all_bd_rows[:limit]

        # ── Done with initial DB queries — close the session NOW so Neon
        #    doesn't kill it during the hours-long Gemini processing loop. ──
        await db.close()

        total = len(all_bd_rows)
        counts = {"total": total, "success": 0, "low_confidence": 0, "no_pdf": 0, "error": 0, "skipped": 0}

        logger.info("FOCUS CEO batch: %d firms to process.", total)

        for index, (bd_id, bd_name, bd_filings_url, bd_cik) in enumerate(all_bd_rows):
            if (index + 1) % 10 == 0 or index == 0:
                logger.info(
                    "FOCUS CEO batch progress: %d/%d (success=%d, no_pdf=%d, error=%d)",
                    index + 1, total, counts["success"], counts["no_pdf"], counts["error"],
                )

            try:
                # ── Slow part: download PDF + Gemini (NO DB connection open) ──
                result = await self._extract_without_db(bd_id, bd_filings_url, bd_cik)
                counts[result.extraction_status] = counts.get(result.extraction_status, 0) + 1

                # ── Quick write: save to DB only if we got a CEO name ──
                if result.ceo_name and result.confidence_score >= 0.4:
                    try:
                        async with SessionLocal() as write_db:
                            await write_db.execute(
                                delete(ExecutiveContact).where(
                                    ExecutiveContact.bd_id == bd_id,
                                    ExecutiveContact.source == "focus_report",
                                )
                            )
                            write_db.add(ExecutiveContact(
                                bd_id=bd_id,
                                name=result.ceo_name,
                                title=(result.ceo_title or "Principal Executive Officer")[:255],
                                email=result.ceo_email,
                                phone=result.ceo_phone,
                                linkedin_url=None,
                                source="focus_report",
                                enriched_at=datetime.now(timezone.utc),
                            ))
                            await write_db.commit()
                    except Exception as db_exc:
                        logger.warning("DB write failed for BD %d, data was extracted but not saved: %s", bd_id, db_exc)

                if result.extraction_status == "success":
                    print(
                        f"  [{index+1}/{total}] {bd_name}: "
                        f"CEO={result.ceo_name}, Phone={result.ceo_phone}, "
                        f"Email={result.ceo_email}, NetCap={result.net_capital} "
                        f"({result.confidence_score:.0%})"
                    )
                elif result.extraction_status == "no_pdf":
                    print(f"  [{index+1}/{total}] {bd_name}: skipped (no X-17A-5 PDF on EDGAR)")
                else:
                    print(f"  [{index+1}/{total}] {bd_name}: {result.extraction_status} -- {result.extraction_notes}")

            except Exception as exc:
                logger.exception("Unexpected error for BD %d (%s)", bd_id, bd_name)
                counts["error"] += 1
                print(f"  [{index+1}/{total}] {bd_name}: CRASH -- {exc}")

            # Respect Gemini rate limits
            if index < total - 1:
                await asyncio.sleep(4.0)

        logger.info("FOCUS CEO batch complete: %s", counts)
        return counts

    async def _upsert_focus_contact(
        self,
        db: AsyncSession,
        *,
        broker_dealer: BrokerDealer,
        ceo_name: str,
        ceo_title: str,
        ceo_email: str | None,
        ceo_phone: str | None,
    ) -> None:
        """Delete any previous focus_report contacts for this BD and insert the new one."""
        await db.execute(
            delete(ExecutiveContact).where(
                ExecutiveContact.bd_id == broker_dealer.id,
                ExecutiveContact.source == "focus_report",
            )
        )
        now = datetime.now(timezone.utc)
        db.add(
            ExecutiveContact(
                bd_id=broker_dealer.id,
                name=ceo_name,
                title=ceo_title[:255],
                email=ceo_email,
                phone=ceo_phone,
                linkedin_url=None,
                source="focus_report",
                enriched_at=now,
            )
        )
        await db.flush()
