"""theHarvester OSINT provider.

Wraps the ``theHarvester`` CLI as a subprocess and parses its JSON output for
discovered emails. Free OSINT sources only (no API keys) — defaults to
``crtsh,rapiddns,otx,duckduckgo``. Override via the ``THEHARVESTER_SOURCES``
env var; tune the per-call timeout via ``THEHARVESTER_TIMEOUT_SECONDS`` (10..300).

Per ADR 0002, error strings are emitted **bare** — no internal ``"theharvester: "``
prefix. The aggregator wraps each error with ``f"{provider.name}: {err}"`` exactly
once when persisting to ``ExtractionRun.error_message``.

Never raises on expected failures: missing binary, no sources configured,
subprocess timeout, FileNotFoundError on exec, non-zero exit, missing or
malformed JSON output — each maps to a single bare error string in
``DiscoveryResult.errors``.

Drafts have ``source="theharvester"``, ``confidence=None`` (theHarvester emits
no confidence score), and ``attribution`` set to ``"theharvester: <sources>"``
capped at 500 chars. Emails are lowercased and deduped; non-string entries
and entries without ``@`` are filtered.

Subprocess invocation goes through the module-level ``_run_subprocess`` helper
so tests can monkeypatch it without spawning real processes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from pathlib import Path

from app.core.config import settings
from app.services.email_extractor.base import DiscoveredEmailDraft, DiscoveryResult

logger = logging.getLogger(__name__)

ATTRIBUTION_CHAR_CAP = 500
STDERR_TAIL_CAP = 200


async def _run_subprocess(cmd: list[str], timeout_seconds: float) -> tuple[int, str, str]:
    """Spawn the subprocess. Returns ``(returncode, stdout, stderr)``.

    Raises ``TimeoutError`` after ``timeout_seconds`` and ``FileNotFoundError``
    if the binary path is invalid. Tests monkeypatch this to avoid real
    subprocesses.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    returncode = proc.returncode if proc.returncode is not None else 0
    return returncode, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


class TheHarvester:
    """``EmailSource`` Protocol implementation backed by the ``theHarvester`` CLI."""

    name = "theharvester"

    async def run(self, domain: str) -> DiscoveryResult:
        binary = shutil.which("theHarvester")
        if binary is None:
            return DiscoveryResult(errors=["binary not installed"])

        sources = settings.theharvester_sources.strip()
        if not sources:
            return DiscoveryResult(errors=["no sources configured"])

        timeout_seconds = float(settings.theharvester_timeout_seconds)
        tempdir = tempfile.mkdtemp(prefix="theharvester_")
        try:
            basename = str(Path(tempdir) / "output")
            cmd = [binary, "-d", domain, "-b", sources, "-f", basename]
            try:
                returncode, _stdout, stderr = await _run_subprocess(cmd, timeout_seconds)
            except TimeoutError:
                return DiscoveryResult(errors=["timeout"])
            except FileNotFoundError:
                return DiscoveryResult(errors=["binary not installed"])
            except Exception as exc:  # noqa: BLE001
                return DiscoveryResult(errors=[f"subprocess error: {exc.__class__.__name__}"])

            if returncode != 0:
                hint_lines = (stderr or "").strip().splitlines()
                hint_tail = hint_lines[-1][:STDERR_TAIL_CAP] if hint_lines else "(no stderr)"
                return DiscoveryResult(errors=[f"non-zero exit {returncode}: {hint_tail}"])

            output_path = Path(f"{basename}.json")
            if not await asyncio.to_thread(output_path.exists):
                return DiscoveryResult(errors=["output file missing"])

            try:
                content = await asyncio.to_thread(output_path.read_text, encoding="utf-8")
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                return DiscoveryResult(errors=[f"invalid json: {exc}"])

            # theHarvester writes the `emails` key only when len(all_emails) > 0
            # (upstream __main__.py @ tag 4.6.0 lines 1210-1211). Missing key
            # means a clean zero-yield run, not a parse failure — return empty
            # success and let the list-type check still catch malformed payloads
            # that put a non-list under the `emails` key.
            raw_emails = payload.get("emails", []) if isinstance(payload, dict) else None
            if raw_emails is None:
                return DiscoveryResult(errors=["payload not a dict"])
            if not isinstance(raw_emails, list):
                return DiscoveryResult(errors=["emails field not a list"])

            attribution = f"theharvester: {sources}"[:ATTRIBUTION_CHAR_CAP]
            seen: set[str] = set()
            drafts: list[DiscoveredEmailDraft] = []
            for entry in raw_emails:
                if not isinstance(entry, str) or "@" not in entry:
                    continue
                lowered = entry.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                drafts.append(
                    DiscoveredEmailDraft(
                        email=lowered,
                        source="theharvester",
                        confidence=None,
                        attribution=attribution,
                    )
                )
            return DiscoveryResult(emails=drafts)
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)
