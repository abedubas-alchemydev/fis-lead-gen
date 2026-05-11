"""Unit tests for the AlertEventBus pub/sub used by the realtime activity feed.

These tests exercise the in-process fan-out independently of Postgres
LISTEN/NOTIFY. The Postgres path is integration-tested via the SSE endpoint
tests in ``test_alerts_stream.py``; here we just need to know that the bus
delivers payloads to subscribers in order and handles backpressure without
wedging on a slow consumer.

Critical invariant: ``subscribe()`` must register the subscriber queue
SYNCHRONOUSLY so a NOTIFY emitted during the SSE handshake gap is queued
rather than dropped. The tests assert this by broadcasting BEFORE the
subscriber's first ``await get()`` and expecting delivery.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.alert_events import (
    AlertEventBus,
    make_alert_inserted_payload,
)


@pytest.mark.asyncio
async def test_broadcast_after_subscribe_is_delivered() -> None:
    """The handshake-gap guard: subscribe() must register before returning,
    so a broadcast issued before the subscriber awaits is still seen."""

    bus = AlertEventBus()
    subscription = bus.subscribe()

    # Broadcast first, then await. If subscribe() registered synchronously
    # this still works; if it deferred registration to the first __anext__
    # the payload would be lost.
    await bus._broadcast(make_alert_inserted_payload([123, 124]))

    received = await asyncio.wait_for(subscription.get(), timeout=1)
    assert received["type"] == "alert.inserted"
    assert received["alert_ids"] == [123, 124]

    subscription.close()


@pytest.mark.asyncio
async def test_subscribers_isolated_from_each_other() -> None:
    bus = AlertEventBus()
    sub_a = bus.subscribe()
    sub_b = bus.subscribe()

    await bus._broadcast({"type": "alert.inserted", "alert_ids": [1]})

    a = await asyncio.wait_for(sub_a.get(), timeout=1)
    b = await asyncio.wait_for(sub_b.get(), timeout=1)
    assert a["alert_ids"] == [1]
    assert b["alert_ids"] == [1]

    sub_a.close()
    sub_b.close()


@pytest.mark.asyncio
async def test_get_returns_none_on_timeout() -> None:
    """The SSE handler relies on ``None`` as a sentinel for "emit a keepalive,
    keep the subscription open". Without this the handler would have to
    catch asyncio.TimeoutError, which kills the generator state."""

    bus = AlertEventBus()
    subscription = bus.subscribe()

    result = await subscription.get(timeout=0.05)
    assert result is None

    # Still alive — a later broadcast must deliver.
    await bus._broadcast({"type": "alert.inserted", "alert_ids": [9]})
    follow_up = await asyncio.wait_for(subscription.get(), timeout=1)
    assert follow_up["alert_ids"] == [9]

    subscription.close()


@pytest.mark.asyncio
async def test_backpressure_drops_oldest_when_queue_fills() -> None:
    """If a subscriber falls behind, the bus must not block other subscribers
    or wedge the broadcast loop. It drops the oldest item, which the client
    recovers via Last-Event-ID replay on the next reconnect."""

    bus = AlertEventBus()
    subscription = bus.subscribe()

    # Saturate the queue without ever reading.
    for i in range(80):
        await bus._broadcast({"type": "alert.inserted", "alert_ids": [i]})

    # The bus did NOT block (the loop above completed). Drain a few items to
    # confirm the most-recently-broadcast events are present even though
    # earlier ones were evicted.
    seen_ids = set()
    while True:
        payload = await subscription.get(timeout=0.05)
        if payload is None:
            break
        seen_ids.update(payload["alert_ids"])

    # The latest broadcast (79) must have survived; the earliest (0) must
    # have been evicted by the backpressure drop.
    assert 79 in seen_ids
    assert 0 not in seen_ids

    subscription.close()


@pytest.mark.asyncio
async def test_close_unregisters_subscriber() -> None:
    """After close(), the queue must leave the broadcast set so subsequent
    broadcasts don't enqueue into a now-orphaned queue."""

    bus = AlertEventBus()
    subscription = bus.subscribe()
    assert len(bus._subscribers) == 1

    subscription.close()
    assert len(bus._subscribers) == 0

    # close() must be idempotent so SSE finally blocks running twice can't
    # raise.
    subscription.close()
    assert len(bus._subscribers) == 0


def test_make_alert_inserted_payload_shape() -> None:
    """Backwards-compat guard on the wire format. Bumps to ``v`` should be
    rolled out with explicit migration steps for any external consumers."""

    payload = make_alert_inserted_payload([7, 8, 9])
    assert payload["v"] == 1
    assert payload["type"] == "alert.inserted"
    assert payload["alert_ids"] == [7, 8, 9]
    assert "ts" in payload
