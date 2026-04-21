"""
CLI entry point.

Usage:
  python -m brokercheck_extractor.cli init-db
  python -m brokercheck_extractor.cli run --limit 10 --save-pdfs
  python -m brokercheck_extractor.cli parse-one --finra-pdf ./firm_5393.pdf
  python -m brokercheck_extractor.cli parse-one --focus-pdf ./xfocus_andpartners.pdf
  python -m brokercheck_extractor.cli fetch-crd 5393 --out ./firm_5393.pdf
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .acquisition.finra_client import FinraClient
from .derivation.clearing_classifier import apply_classification
from .orchestrator import run_batch
from .orchestrator_hybrid import run_hybrid_batch
from .parsers.finra_parser import parse_finra_pdf
from .parsers.focus_parser import parse_focus_pdf
from .storage.db import init_schema


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _jsonify(obj):
    """JSON encoder that understands Decimal, date, datetime."""
    from datetime import date, datetime
    from decimal import Decimal
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj)}")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

async def cmd_init_db(args: argparse.Namespace) -> int:
    await init_schema()
    print("Schema initialized.")
    return 0


async def cmd_run(args: argparse.Namespace) -> int:
    stats = await run_batch(
        where_status=args.where_status,
        limit=args.limit,
        save_raw_pdfs=args.save_pdfs,
    )
    print(stats.summary())
    return 0 if stats.failed == 0 else 2


async def cmd_run_hybrid(args: argparse.Namespace) -> int:
    """Run the full hybrid pipeline: deterministic + Gemini cross-validation."""
    stats = await run_hybrid_batch(
        where_status=args.where_status,
        limit=args.limit,
        save_raw_pdfs=args.save_pdfs,
        enable_llm=not args.no_llm,
    )
    print(stats.summary())
    return 0 if stats.failed == 0 else 2


def cmd_parse_one(args: argparse.Namespace) -> int:
    if args.finra_pdf:
        pdf_bytes = Path(args.finra_pdf).read_bytes()
        profile = parse_finra_pdf(pdf_bytes)
        apply_classification(profile)
        print(json.dumps(profile.model_dump(mode="json"), default=_jsonify, indent=2))
        return 0

    if args.focus_pdf:
        pdf_bytes = Path(args.focus_pdf).read_bytes()
        report = parse_focus_pdf(pdf_bytes)
        print(json.dumps(report.model_dump(mode="json"), default=_jsonify, indent=2))
        return 0

    print("Provide --finra-pdf or --focus-pdf", file=sys.stderr)
    return 1


async def cmd_fetch_crd(args: argparse.Namespace) -> int:
    async with FinraClient() as finra:
        pdf_bytes = await finra.download_pdf(args.crd)
    Path(args.out).write_bytes(pdf_bytes)
    print(f"Downloaded {len(pdf_bytes):,} bytes -> {args.out}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(prog="brokercheck_extractor")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="Create DB tables")

    p_run = sub.add_parser("run", help="Batch-process all firms (deterministic only)")
    p_run.add_argument("--where-status", default=None, help="Filter firms_input.status")
    p_run.add_argument("--limit", type=int, default=None, help="Max firms to process")
    p_run.add_argument("--save-pdfs", action="store_true", help="Save raw PDFs to disk")

    p_hy = sub.add_parser("run-hybrid", help="Deterministic + Gemini cross-validated pipeline")
    p_hy.add_argument("--where-status", default=None)
    p_hy.add_argument("--limit", type=int, default=None)
    p_hy.add_argument("--save-pdfs", action="store_true")
    p_hy.add_argument("--no-llm", action="store_true", help="Disable Gemini (debug only)")

    p_po = sub.add_parser("parse-one", help="Parse a single local PDF and print JSON")
    p_po.add_argument("--finra-pdf", help="Path to a BrokerCheck PDF")
    p_po.add_argument("--focus-pdf", help="Path to an X-17A-5 PDF")

    p_fc = sub.add_parser("fetch-crd", help="Download the BrokerCheck PDF for a CRD")
    p_fc.add_argument("crd", help="FINRA CRD number")
    p_fc.add_argument("--out", required=True, help="Output file path")

    args = parser.parse_args()
    setup_logging(args.log_level)

    handlers = {
        "init-db": cmd_init_db,
        "run": cmd_run,
        "run-hybrid": cmd_run_hybrid,
        "parse-one": cmd_parse_one,
        "fetch-crd": cmd_fetch_crd,
    }
    handler = handlers[args.cmd]
    if asyncio.iscoroutinefunction(handler):
        return asyncio.run(handler(args))
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
