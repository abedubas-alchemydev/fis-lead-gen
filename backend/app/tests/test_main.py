"""Tests for the FastAPI application bootstrap — lifespan hook in particular.

Ref: .claude/focus-fix/diagnosis.md §9 ticket S-3.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_lifespan_disposes_sqlalchemy_engine_on_shutdown() -> None:
    """On TestClient exit, the app's lifespan shutdown must run and call
    engine.dispose() exactly once. Regression guard: previously the engine
    was created at module import but never disposed, leaving Neon to reclaim
    connections via idle detection on Cloud Run revision swap."""
    with patch("app.main.engine") as mock_engine:
        mock_engine.dispose = AsyncMock()
        from app.main import app  # Import after patch so lifespan sees the mock.

        with TestClient(app):
            # Entering the context triggers startup; exiting triggers shutdown.
            pass

        mock_engine.dispose.assert_awaited_once()


def test_lifespan_swallows_dispose_errors() -> None:
    """Shutdown must not propagate engine.dispose() exceptions — the server
    is terminating anyway, and a noisy shutdown exception would mask clean-
    termination signals in Cloud Run logs. Propagation would also break any
    test harness that exits via TestClient's context manager."""
    with patch("app.main.engine") as mock_engine:
        mock_engine.dispose = AsyncMock(side_effect=RuntimeError("simulated pool failure"))
        from app.main import app

        # Should NOT raise.
        with TestClient(app):
            pass

        mock_engine.dispose.assert_awaited_once()


def test_health_endpoint_still_returns_ok() -> None:
    """Sanity: adding lifespan did not change the /health behavior."""
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
