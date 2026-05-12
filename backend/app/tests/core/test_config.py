"""Tests pinning the production Gemini extraction model default and env-var override.

Tier 1 of the Gemini paid plan caps ``gemini-2.5-pro`` at 1,000 RPD but allows
``gemini-2.5-flash`` at 10,000 RPD with the same JSON-schema + multi-modal
capabilities. The production default is Flash so the clearing and financial
pipelines can run the full universe in a single day at ~5x lower per-call cost.
The override path (``GEMINI_PDF_MODEL`` env var) must remain intact so we can
flip back to Pro on Cloud Run for ad-hoc reruns when Pro quota is healthy and
the deeper reasoning chain is wanted on rationale-heavy ambiguous extractions.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


def test_default_gemini_pdf_model_is_flash(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Settings()`` resolves to ``gemini-2.5-flash`` when no env override is set.

    ``Settings()`` is instantiated directly here rather than via
    ``get_settings()``: the latter is ``lru_cache``-wrapped and would return a
    stale instance whose ``gemini_pdf_model`` reflects whatever the env looked
    like at first call within the pytest session.
    """
    monkeypatch.delenv("GEMINI_PDF_MODEL", raising=False)

    assert Settings().gemini_pdf_model == "gemini-2.5-flash"


def test_gemini_pdf_model_env_override_to_pro_is_honored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GEMINI_PDF_MODEL=gemini-2.5-pro`` flips the field back to Pro.

    Regression guard for the documented Cloud Run escape hatch. If a future
    refactor adds an alias or freezes the field default it would break this
    override and silently strand any Pro rerun on Flash.
    """
    monkeypatch.setenv("GEMINI_PDF_MODEL", "gemini-2.5-pro")

    assert Settings().gemini_pdf_model == "gemini-2.5-pro"
