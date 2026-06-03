"""Unified backfill driver — one Cloud Run Job that sequences every phase so
BD + 13F IA profiles are pre-filled (no more manual Enrich / Generate More
Details / on-view gap-fill).

Design: shells out to the proven per-phase scripts rather than re-implementing
their batching / cooldown logic, then prints a unified summary. ``--scan-only``
is a no-spend pre-flight across all phases. Phases advance independently across
daily runs (each script's own cooldown / dedup), draining the backlog over
~3-4 weeks, then settling into maintenance.

Phases (see plans/backfill-all-bd-ia-2026-05-29.md):
  P1  BD refresh-all     scripts.gap_fill_broker_dealers       FOCUS financials + FOCUS clearing + FINRA health + website + contacts + filings
  P2  FINRA reconcile    scripts.backfill_finra_clearing       authoritative clearing partner (--skip-verified to advance)
  P3  IA refresh-all     scripts.gap_fill_investment_advisors  Form ADV: officers/owners/client_types/client_counts + website + filings + contacts
  P5  Phones (metered)   scripts.gap_fill_phones               Apollo per-reveal + PDL phones; ≤50 firms/run + reveal cap
  P4  IA roster (opt-in) scripts.backfill_advisor_roster_contacts   one-time catch-up; only with --include-roster

A zombie-run reaper (services/pipeline_reaper) is a SEPARATE concern — this
driver does not reap.

Usage (from repo root / Cloud Run Job entrypoint):
    python -m scripts.backfill_all --scan-only
    python -m scripts.backfill_all --apply
    python -m scripts.backfill_all --apply --bd-limit 250 --ia-limit 250 --phone-limit 50
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_MODULES = {
    "p1": ("scripts.gap_fill_broker_dealers", "P1 BD refresh-all (FOCUS financials+clearing, FINRA, website, contacts, filings)"),
    "p2": ("scripts.backfill_finra_clearing", "P2 FINRA clearing reconcile (authoritative partner)"),
    "p3": ("scripts.gap_fill_investment_advisors", "P3 IA refresh-all (Form ADV incl client_counts, website, filings, contacts)"),
    "p4": ("scripts.backfill_advisor_roster_contacts", "P4 IA roster contacts (one-time catch-up)"),
    "p5": ("scripts.gap_fill_phones", "P5 Phones (metered Apollo reveal + PDL)"),
}


def _args_for(key: str, a: argparse.Namespace, apply: bool) -> list[str]:
    bd, ia, ph, rc = str(a.bd_limit), str(a.ia_limit), str(a.phone_limit), str(a.reveal_cap)
    if key == "p1":
        return ["--apply", "--limit", bd] if apply else ["--scan-only"]
    if key == "p2":
        # backfill_finra_clearing uses absence-of-`--apply` as its dry-run.
        base = ["--skip-verified", "--limit", bd]
        return ["--apply", *base] if apply else base
    if key == "p3":
        return ["--apply", "--limit", ia] if apply else ["--scan-only"]
    if key == "p4":
        return ["--apply", "--limit", ia] if apply else ["--scan-only"]
    if key == "p5":
        base = ["--limit", ph, "--reveal-cap", rc]
        return ["--apply", *base] if apply else ["--scan-only", *base]
    raise ValueError(f"unknown phase {key!r}")


def main() -> int:
    p = argparse.ArgumentParser(description="Unified BD + 13F-IA backfill driver.")
    p.add_argument("--apply", action="store_true", help="Run the phases for real (API spend).")
    p.add_argument("--scan-only", action="store_true", help="No-spend pre-flight across all phases.")
    p.add_argument("--bd-limit", type=int, default=250, help="Firms/run for P1 + P2 (default 250).")
    p.add_argument("--ia-limit", type=int, default=250, help="Firms/run for P3 + P4 (default 250).")
    p.add_argument("--phone-limit", type=int, default=50, help="Firms/run for P5 phones (default 50).")
    p.add_argument("--reveal-cap", type=int, default=300, help="Soft cap on est. reveals/run for P5 (default 300).")
    p.add_argument("--include-roster", action="store_true", help="Also run P4 roster catch-up.")
    p.add_argument("--phases", default=None, help="Comma list e.g. 'p1,p5'. Default p1,p2,p3,p5 (+p4 if --include-roster).")
    a = p.parse_args()

    apply = a.apply and not a.scan_only
    if a.phases:
        phases = [x.strip() for x in a.phases.split(",") if x.strip()]
    else:
        phases = ["p1", "p2", "p3", "p5"]
        if a.include_roster:
            phases.insert(3, "p4")

    mode = "APPLY" if apply else "SCAN-ONLY"
    print(f"=== backfill_all [{mode}] phases={phases} "
          f"bd={a.bd_limit} ia={a.ia_limit} phones={a.phone_limit} reveal_cap={a.reveal_cap} ===",
          flush=True)
    if not apply:
        print("(scan-only: no writes, no API spend)", flush=True)

    results: list[tuple[str, int]] = []
    for key in phases:
        module, label = _MODULES[key]
        cmd = [sys.executable, "-m", module, *_args_for(key, a, apply)]
        print(f"\n----- {label} -----\n$ {' '.join(cmd)}", flush=True)
        rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
        results.append((label, rc))
        print(f"----- {label}: exit {rc} -----", flush=True)

    print("\n=== SUMMARY ===", flush=True)
    failed = 0
    for label, rc in results:
        if rc != 0:
            failed += 1
        print(f"  {'ok ' if rc == 0 else 'ERR'}  {label}" + ("" if rc == 0 else f" (exit {rc})"))
    if failed:
        print(f"\n{failed} phase(s) failed — a later run retries (cooldowns advance the rest).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
