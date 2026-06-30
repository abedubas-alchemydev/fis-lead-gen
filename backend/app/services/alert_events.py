from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

NOTIFY_CHANNEL = "filing_alerts_new"
_SUBSCRIBER_QUEUE_MAXSIZE = 64
_RECONNECT_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)
# A single dropped LISTEN connection is routine (Neon idle-suspend / brief
# platform blip); only escalate the reconnect log from INFO to WARNING once
# this many consecutive reconnects have failed, which points to a real outage.
_RECONNECT_QUIET_ATTEMPTS = 3

# Neon (and intermediate network paths) close idle PG sessions after ~5 min.
# Two-layer defense keeps the LISTEN session alive:
#   1. TCP keepalives via libpq — kernel sends probes after 30s of silence,
#      well before Neon's idle threshold.
#   2. Application-level heartbeat — a no-op SELECT every ~60s ensures
#      protocol-level traffic even when the network path swallows TCP
#      keepalive ACKs (some serverless edges have done so historically).
_LISTEN_KEEPALIVE_PARAMS: dict[str, int] = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}
_LISTEN_HEARTBEAT_SECONDS = 60.0


def _raw_psycopg_dsn() -> str:
    """Translate the SQLAlchemy URL into the raw libpq DSN psycopg expects.

    SQLAlchemy uses ``postgresql+psycopg://...`` / ``postgresql+asyncpg://...``;
    psycopg3 wants a plain ``postgresql://...``. We strip the ``+driver`` token
    in one place so the listener uses the same Neon DB as every other code
    path.
    """
    url = settings.database_url
    if url.startswith("postgresql+"):
        scheme, _, rest = url.partition("://")
        return "postgresql://" + rest
    return url


class AlertSubscription:
    """Per-subscriber handle held by the SSE generator.

    Registered synchronously by :meth:`AlertEventBus.subscribe` so the
    subscriber queue is in the broadcast set BEFORE the caller suspends
    on its first ``await``. Without this guarantee a NOTIFY that landed
    during the handshake gap would be silently dropped.
    """

    def __init__(self, queue: asyncio.Queue[dict[str, Any]], bus: "AlertEventBus") -> None:
        self._queue = queue
        self._bus = bus
        self._closed = False

    async def get(self, *, timeout: float | None = None) -> dict[str, Any] | None:
        """Pull the next payload, optionally with a per-call timeout.

        Returns ``None`` on timeout so the caller can interleave keep-alive
        frames without unwinding the generator state. Without the timeout
        path, ``asyncio.wait_for`` on an async-generator ``__anext__`` would
        cancel the generator's body on every keep-alive and break the
        subscription.
        """
        if timeout is None:
            return await self._queue.get()
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus._unregister(self._queue)


class AlertEventBus:
    """Process-local fan-out for filing-alert NOTIFY messages.

    One ``run_listener`` task per Cloud Run instance holds a dedicated psycopg3
    async connection (outside the SQLAlchemy pool) running ``LISTEN
    filing_alerts_new``. Notifications are decoded once and pushed to every
    subscriber's local queue. Writers call :meth:`publish_via_session` to
    ``pg_notify`` inside their own transaction so the notification only
    delivers on commit — across instances the LISTEN connections receive it
    even though the writer hit a different instance.
    """

    def __init__(self) -> None:
        # CPython's set add/discard are atomic for the GIL-protected critical
        # section, so we don't need a lock around membership churn here.
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> AlertSubscription:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        return AlertSubscription(queue, self)

    def _unregister(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish_via_session(self, db: AsyncSession, payload: dict[str, Any]) -> None:
        """Fire NOTIFY inside the caller's transaction.

        The notification is buffered by Postgres until COMMIT, which is exactly
        the boundary we want: subscribers should only see alerts that durably
        landed. We do NOT call ``conn.commit()`` here — the caller owns the
        transaction.
        """
        encoded = json.dumps(payload, separators=(",", ":"))
        await db.execute(
            text("SELECT pg_notify(:channel, :payload)"),
            {"channel": NOTIFY_CHANNEL, "payload": encoded},
        )

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        # Snapshot the set so a concurrent subscribe/unsubscribe during fan-out
        # doesn't mutate the iterator under us.
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop oldest and try again so the slow subscriber doesn't
                # wedge the bus. Client's Last-Event-ID replay catches the
                # gap on the next reconnect.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    logger.warning("alert subscriber still full after drop; skipping")

    async def run_listener(self) -> None:
        """Long-lived task: LISTEN on a dedicated connection, fan out forever.

        Reconnect-with-backoff on any failure (Neon idle suspend, network
        blip, server restart). Cancellation from ``lifespan`` shutdown
        propagates out cleanly.
        """
        attempt = 0
        while True:
            try:
                await self._listen_once()
                attempt = 0  # clean exit means EOF on the connection — retry immediately
            except asyncio.CancelledError:
                raise
            except psycopg.OperationalError as exc:
                # A dropped LISTEN socket is routine for a long-lived Neon
                # connection (idle suspend, a brief platform blip, a server
                # restart). The loop just reconnects, so a single drop is
                # self-healing, not an incident — log it concisely WITHOUT a
                # traceback so it doesn't surface as an ERROR in Cloud Run.
                # Escalate to a full WARNING only if reconnects keep failing,
                # which signals a real outage rather than a transient blip.
                delay = _RECONNECT_BACKOFF_SECONDS[min(attempt, len(_RECONNECT_BACKOFF_SECONDS) - 1)]
                attempt += 1
                if attempt <= _RECONNECT_QUIET_ATTEMPTS:
                    logger.info(
                        "alert_events listener connection dropped (%s); reconnecting in %.1fs",
                        type(exc).__name__,
                        delay,
                    )
                else:
                    logger.warning(
                        "alert_events listener still down after %d attempts (%s); retrying in %.1fs",
                        attempt,
                        type(exc).__name__,
                        delay,
                        exc_info=True,
                    )
                await asyncio.sleep(delay)
            except Exception:
                delay = _RECONNECT_BACKOFF_SECONDS[min(attempt, len(_RECONNECT_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "alert_events listener crashed; retrying in %.1fs",
                    delay,
                    exc_info=True,
                )
                attempt += 1
                await asyncio.sleep(delay)

    async def _listen_once(self) -> None:
        dsn = _raw_psycopg_dsn()
        # autocommit=True: LISTEN must run outside a transaction or it sees
        # no notifications until COMMIT. psycopg3's `notifies()` then blocks
        # in libpq until a NOTIFY arrives.
        async with await psycopg.AsyncConnection.connect(
            dsn,
            autocommit=True,
            **_LISTEN_KEEPALIVE_PARAMS,
        ) as conn:
            await conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
            logger.info("alert_events listener ready on channel %s", NOTIFY_CHANNEL)
            while True:
                # notifies(timeout=N) yields each NOTIFY as it arrives and
                # exits the inner iteration once N seconds pass without a
                # new one. On exit we emit a no-op SELECT to keep the
                # session active across Neon's idle threshold — TCP
                # keepalives alone aren't enough when intermediaries drop
                # silent sockets.
                saw_notification = False
                async for notify in conn.notifies(timeout=_LISTEN_HEARTBEAT_SECONDS):
                    saw_notification = True
                    try:
                        payload = json.loads(notify.payload)
                    except json.JSONDecodeError:
                        logger.warning(
                            "alert_events: dropping malformed NOTIFY payload"
                        )
                        continue
                    await self._broadcast(payload)
                if not saw_notification:
                    await conn.execute("SELECT 1")


alert_event_bus = AlertEventBus()


def make_alert_inserted_payload(alert_ids: list[int]) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "alert.inserted",
        "alert_ids": alert_ids,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
