"""Tests for the SSE generator behind ``GET /api/v1/alerts/stream``.

The handler composes three pieces of behavior:

  1. Authentication — the handshake runs the cookie auth check against a
     short-lived session BEFORE opening the StreamingResponse, so a missing
     session token gets a clean 401 (instead of an opaque hanging stream).
  2. Bus → frame translation — payloads from the in-process
     ``alert_event_bus`` get hydrated through the repository's projection
     and emitted as SSE frames with the correct ``event:``, ``id:``, and
     ``data:`` lines.
  3. Keep-alive — when no notification has arrived for the keepalive
     window, the generator emits a comment frame so the connection isn't
     pruned by intermediate proxies.

We exercise the async generator directly rather than through httpx's
ASGI transport. The transport's streaming path interacts awkwardly with
Starlette's StreamingResponse in some environments — and the underlying
contract we care about is the sequence of frames the generator yields,
not the transport. Postgres LISTEN/NOTIFY is exercised separately via
integration-marked tests.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

import app.api.v1.endpoints.alerts as alerts_endpoint
from app.schemas.alerts import AlertListItem
from app.services.alert_events import AlertEventBus


def _alert(alert_id: int) -> AlertListItem:
    return AlertListItem(
        id=alert_id,
        bd_id=1,
        firm_name=f"Firm {alert_id}",
        form_type="Form BD",
        priority="high",
        filed_at=datetime(2026, 5, 11),
        summary="alert summary",
        source_filing_url=None,
        is_read=False,
    )


@pytest.fixture
def isolated_bus(monkeypatch: pytest.MonkeyPatch) -> AlertEventBus:
    """Replace the module-level alert_event_bus with a fresh instance so the
    test cannot be polluted by other suites that may have left subscribers
    or pending broadcasts on the shared singleton."""
    fresh = AlertEventBus()
    monkeypatch.setattr(alerts_endpoint, "alert_event_bus", fresh)
    return fresh


@pytest.fixture
def fake_hydrate(monkeypatch: pytest.MonkeyPatch):
    async def _hydrate(alert_ids: list[int]) -> list[AlertListItem]:
        return [_alert(i) for i in alert_ids]

    monkeypatch.setattr(alerts_endpoint, "_hydrate_alerts", _hydrate)
    return _hydrate


@pytest.fixture
def fake_replay(monkeypatch: pytest.MonkeyPatch):
    async def _replay(last_event_id: int) -> list[AlertListItem]:
        return [_alert(last_event_id + 1)]

    monkeypatch.setattr(alerts_endpoint, "_replay_missed_alerts", _replay)
    return _replay


def _parse_sse_frames(raw: str) -> list[dict[str, str]]:
    """Parse a buffer of SSE frames into [{event, id?, data?, comment?}].

    Comment lines start with ``:`` and have no event name; we expose them
    as ``{"comment": "..."}`` so keep-alive frames can be asserted.
    """
    frames: list[dict[str, str]] = []
    for block in raw.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        frame: dict[str, str] = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                frame["comment"] = line[1:].strip()
            elif line.startswith("event:"):
                frame["event"] = line[len("event:") :].strip()
            elif line.startswith("id:"):
                frame["id"] = line[len("id:") :].strip()
            elif line.startswith("data:"):
                frame["data"] = line[len("data:") :].strip()
        frames.append(frame)
    return frames


async def _collect_frames(gen, *, count: int, timeout: float = 2.0) -> list[dict[str, str]]:
    buffer = ""
    deadline = asyncio.get_running_loop().time() + timeout
    while buffer.count("\n\n") < count:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            seen = buffer.count("\n\n")
            raise asyncio.TimeoutError(
                f"only saw {seen} frames after {timeout}s: {buffer!r}"
            )
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
        buffer += chunk
    return _parse_sse_frames(buffer)


@pytest.mark.asyncio
async def test_stream_emits_ready_frame_then_inserted_alerts(
    isolated_bus: AlertEventBus, fake_hydrate
) -> None:
    """When a payload arrives on the bus, the SSE generator should hydrate
    the ids and yield an ``alert.inserted`` frame with the items array.
    The very first frame must be ``event: ready`` so the proxy flushes
    its first chunk and the browser's onopen handler fires."""

    gen = alerts_endpoint._alert_event_stream(None)

    # First frame is "ready" and arrives synchronously.
    first = await asyncio.wait_for(gen.__anext__(), timeout=1)
    assert "event: ready" in first

    # Push a payload onto the (now-subscribed) bus.
    await isolated_bus._broadcast(
        {"type": "alert.inserted", "alert_ids": [42, 43]}
    )

    second = await asyncio.wait_for(gen.__anext__(), timeout=1)
    frames = _parse_sse_frames(second)
    assert len(frames) == 1
    frame = frames[0]
    assert frame["event"] == "alert.inserted"
    assert frame["id"] == "43"  # max(id) in the payload

    items = json.loads(frame["data"])["items"]
    assert {item["id"] for item in items} == {42, 43}
    assert {item["firm_name"] for item in items} == {"Firm 42", "Firm 43"}

    await gen.aclose()


@pytest.mark.asyncio
async def test_stream_replays_via_last_event_id(
    isolated_bus: AlertEventBus, fake_hydrate, fake_replay
) -> None:
    """A reconnect with a ``Last-Event-ID`` header value should immediately
    replay alerts whose id > N, with no need for a subsequent broadcast."""

    gen = alerts_endpoint._alert_event_stream("99")
    frames = await _collect_frames(gen, count=2)

    events = [f.get("event") for f in frames]
    assert "ready" in events
    inserted = next(f for f in frames if f.get("event") == "alert.inserted")
    # fake_replay returns one alert at last_event_id + 1 = 100.
    assert inserted["id"] == "100"
    assert "Firm 100" in inserted["data"]

    await gen.aclose()


@pytest.mark.asyncio
async def test_stream_emits_keepalive_when_idle(
    isolated_bus: AlertEventBus, fake_hydrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no payload arrives within the keep-alive window the generator
    must emit a comment frame instead of letting the connection idle. We
    shrink the window to keep the test fast."""

    monkeypatch.setattr(alerts_endpoint, "_STREAM_KEEPALIVE_SECONDS", 0.1)

    gen = alerts_endpoint._alert_event_stream(None)
    first = await asyncio.wait_for(gen.__anext__(), timeout=1)
    assert "event: ready" in first

    second = await asyncio.wait_for(gen.__anext__(), timeout=1)
    assert second.startswith(":")  # SSE comment frame

    await gen.aclose()


@pytest.mark.asyncio
async def test_stream_self_closes_at_max_duration(
    isolated_bus: AlertEventBus, fake_hydrate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cloud Run's HTTP/1 deadline caps requests at 60 min; the generator
    must self-close before the platform does so the client reconnect runs
    on graceful EOF, not a 503."""

    monkeypatch.setattr(alerts_endpoint, "_STREAM_MAX_SECONDS", 0)
    monkeypatch.setattr(alerts_endpoint, "_STREAM_KEEPALIVE_SECONDS", 0.05)

    gen = alerts_endpoint._alert_event_stream(None)
    await asyncio.wait_for(gen.__anext__(), timeout=1)  # ready

    # Second __anext__ should StopAsyncIteration cleanly because the elapsed
    # check returns immediately with _STREAM_MAX_SECONDS=0.
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(gen.__anext__(), timeout=1)
