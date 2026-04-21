"""theHarvester provider tests.

Subprocess interactions are stubbed via ``monkeypatch.setattr`` on the
module-level ``_run_subprocess`` helper; ``shutil.which`` is patched so no
real binary is required. The fake subprocess optionally writes the JSON
output file to the path passed via ``-f`` so file-read paths exercise their
real logic.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from app.core import config
from app.services.email_extractor import theharvester


def _set_sources(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(config.settings, "theharvester_sources", value)


def _set_timeout(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    monkeypatch.setattr(config.settings, "theharvester_timeout_seconds", value)


def _patch_binary_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/theHarvester")


def _patch_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)


def _make_fake_run(
    *,
    emails: list[Any] | None = None,
    raw_payload: object | None = None,
    write_invalid_json: bool = False,
    skip_file: bool = False,
    returncode: int = 0,
    stderr: str = "",
    raise_exc: BaseException | None = None,
) -> Callable[[list[str], float], Awaitable[tuple[int, str, str]]]:
    """Build a fake ``_run_subprocess`` that optionally writes an output file."""

    async def fake(cmd: list[str], _timeout: float) -> tuple[int, str, str]:
        if raise_exc is not None:
            raise raise_exc
        if not skip_file:
            f_idx = cmd.index("-f")
            output_path = Path(f"{cmd[f_idx + 1]}.json")
            if write_invalid_json:
                await asyncio.to_thread(output_path.write_text, "not json{")
            elif raw_payload is not None:
                await asyncio.to_thread(output_path.write_text, json.dumps(raw_payload))
            elif emails is not None:
                await asyncio.to_thread(output_path.write_text, json.dumps({"emails": emails}))
        return returncode, "", stderr

    return fake


def _setup_with_fake(
    monkeypatch: pytest.MonkeyPatch,
    fake: Callable[[list[str], float], Awaitable[tuple[int, str, str]]],
) -> None:
    _set_sources(monkeypatch, "crtsh,rapiddns")
    _set_timeout(monkeypatch, 90)
    _patch_binary_present(monkeypatch)
    monkeypatch.setattr(theharvester, "_run_subprocess", fake)


# --- Happy paths -----------------------------------------------------------


async def test_happy_path_two_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(emails=["alice@example.com", "bob@example.com"]))

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == []
    emails = sorted(d.email for d in result.emails)
    assert emails == ["alice@example.com", "bob@example.com"]
    by_email = {d.email: d for d in result.emails}
    assert by_email["alice@example.com"].source == "theharvester"
    assert by_email["alice@example.com"].confidence is None
    assert by_email["alice@example.com"].attribution == "theharvester: crtsh,rapiddns"


async def test_dedupe_and_lowercase(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(
        monkeypatch,
        _make_fake_run(emails=["Alice@Example.com", "alice@example.com", "BOB@Example.com"]),
    )

    result = await theharvester.TheHarvester().run("example.com")

    emails = sorted(d.email for d in result.emails)
    assert emails == ["alice@example.com", "bob@example.com"]


async def test_non_string_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(
        monkeypatch,
        _make_fake_run(emails=["not-an-email", "alice@x.com", None, 42, ""]),
    )

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == []
    assert [d.email for d in result.emails] == ["alice@x.com"]


# --- Error branches --------------------------------------------------------


async def test_binary_not_installed_via_which(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sources(monkeypatch, "crtsh")
    _patch_binary_missing(monkeypatch)

    result = await theharvester.TheHarvester().run("example.com")

    assert result.emails == []
    assert result.errors == ["binary not installed"]


async def test_binary_not_installed_via_filenotfound(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(raise_exc=FileNotFoundError("theHarvester not found")))

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == ["binary not installed"]


async def test_subprocess_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generic Exception during subprocess invocation → bare 'subprocess error: <type>'."""
    _setup_with_fake(monkeypatch, _make_fake_run(raise_exc=PermissionError("mock")))

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == ["subprocess error: PermissionError"]


async def test_no_sources_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sources(monkeypatch, "   ")
    _patch_binary_present(monkeypatch)

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == ["no sources configured"]


async def test_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(raise_exc=TimeoutError()))

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == ["timeout"]


async def test_non_zero_exit_empty_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-zero exit with empty stderr falls back to '(no stderr)' marker."""
    _setup_with_fake(monkeypatch, _make_fake_run(returncode=1, stderr="", skip_file=True))

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == ["non-zero exit 1: (no stderr)"]


async def test_non_zero_exit_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-zero exit with stderr content trims to last non-empty line."""
    _setup_with_fake(
        monkeypatch,
        _make_fake_run(returncode=2, stderr="some warning\nfatal: bad source\n", skip_file=True),
    )

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == ["non-zero exit 2: fatal: bad source"]


async def test_output_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(skip_file=True))

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == ["output file missing"]


async def test_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(write_invalid_json=True))

    result = await theharvester.TheHarvester().run("example.com")

    assert len(result.errors) == 1
    assert result.errors[0].startswith("invalid json:")


async def test_no_emails_key_yields_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """theHarvester upstream omits the 'emails' key on zero-yield runs (tag 4.6.0
    __main__.py:1210-1211). Missing key = empty success, not an error."""
    _setup_with_fake(monkeypatch, _make_fake_run(raw_payload={"hosts": []}))

    result = await theharvester.TheHarvester().run("example.com")

    assert result.emails == []
    assert result.errors == []


async def test_emails_key_explicitly_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the present-but-empty path so the missing-key fix doesn't regress it."""
    _setup_with_fake(monkeypatch, _make_fake_run(raw_payload={"emails": [], "hosts": []}))

    result = await theharvester.TheHarvester().run("example.com")

    assert result.emails == []
    assert result.errors == []


async def test_emails_not_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(raw_payload={"emails": "not-a-list"}))

    result = await theharvester.TheHarvester().run("example.com")

    assert result.errors == ["emails field not a list"]


# --- ADR 0002 contract: bare errors across every error fixture -------------


def _setup_binary_missing_via_which(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sources(monkeypatch, "crtsh")
    _patch_binary_missing(monkeypatch)


def _setup_no_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_sources(monkeypatch, "")
    _patch_binary_present(monkeypatch)


def _setup_filenotfound(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(raise_exc=FileNotFoundError()))


def _setup_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(raise_exc=TimeoutError()))


def _setup_nonzero_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(returncode=1, stderr="oops\n", skip_file=True))


def _setup_nonzero_empty_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(returncode=1, stderr="", skip_file=True))


def _setup_subprocess_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(raise_exc=PermissionError("mock")))


def _setup_output_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(skip_file=True))


def _setup_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(write_invalid_json=True))


def _setup_emails_not_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_with_fake(monkeypatch, _make_fake_run(raw_payload={"emails": "not-a-list"}))


@pytest.mark.parametrize(
    "setup_fn",
    [
        _setup_binary_missing_via_which,
        _setup_no_sources,
        _setup_filenotfound,
        _setup_timeout,
        _setup_subprocess_generic_exception,
        _setup_nonzero_empty_stderr,
        _setup_nonzero_with_stderr,
        _setup_output_missing,
        _setup_invalid_json,
        _setup_emails_not_list,
    ],
)
async def test_bare_error_contract(
    monkeypatch: pytest.MonkeyPatch, setup_fn: Callable[[pytest.MonkeyPatch], None]
) -> None:
    """ADR 0002: no error string from run() may start with 'theharvester:' or 'theharvester '."""
    setup_fn(monkeypatch)
    result = await theharvester.TheHarvester().run("example.com")
    assert len(result.errors) == 1
    err = result.errors[0]
    assert not err.startswith("theharvester:"), f"bare-error contract violated: {err!r}"
    assert not err.startswith("theharvester "), f"bare-error contract violated: {err!r}"
