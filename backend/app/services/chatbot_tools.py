"""Doxie chatbot tool registry — read-only lookups against BD / IA / II repos.

Each tool is a thin wrapper around an existing repository method, gated on
the matching per-user feature permission. Tools are dispatched by
``ChatbotService`` during Gemini function-calling; results come back as
JSON-able dicts that Gemini incorporates into its reply.

Design rules followed throughout this module:

- Tools **never raise** into the Gemini iteration loop. Every expected
  failure (permission denied, bad args, missing row, repo exception) is
  caught and returned as a structured ``{"error": "...", "message": "..."}``
  dict. The model gets to surface it gracefully ("you don't have access to
  that data") instead of the whole chat 502'ing.
- Tool results are **compact hand-projected dicts**, not full Pydantic
  ``model_dump()`` payloads. A search returning 10 firms × 30 fields would
  eat the prompt budget; we cap to ~10 fields per summary row and ~22 per
  profile. The schema-drift guard in ``test_chatbot_tools.py`` catches
  accidental key renames on the underlying Pydantic schemas.
- All projections pass through ``_jsonable`` so ``Decimal`` / ``date`` /
  ``datetime`` values get pre-serialized before httpx tries to JSON-encode
  them — httpx's default encoder would otherwise raise ``TypeError``.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feature_permissions import (
    ALERTS,
    INSTITUTIONAL_INVESTORS,
    INVESTMENT_ADVISORS,
    INVESTORS,
    MASTER_LIST,
    VAULT,
)
from app.models.advisor_filing import AdvisorFiling
from app.models.form4_transaction import Form4Transaction
from app.models.investor_filing import InvestorFiling
from app.models.vault_folder import VaultFolder
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import (
    BrokerDealerListItem,
    FinancialMetricItem,
)
from app.schemas.institutional_investor import InstitutionalInvestorListItem
from app.schemas.investment_advisor import InvestmentAdvisorListItem
from app.schemas.pipeline import ClearingArrangementItem
from app.services.alerts import AlertRepository
from app.services.auth import ensure_feature
from app.services.broker_dealers import BrokerDealerRepository
from app.services.chatbot_semantic import (
    ChatbotSemanticService,
    ENTITY_TYPE_BROKER_DEALER,
)
from app.services.chatbot_urls import (
    ALERTS_URL,
    VAULT_URL,
    bd_detail_url,
    bd_list_url,
    ia_detail_url,
    ia_list_url,
    ii_detail_url,
    investors_url,
)
from app.services.finra_pdf_service import (
    FinraPdfFetchError,
    FinraPdfNotFound,
    fetch_brokercheck_pdf,
)
from app.services.form4_transactions import Form4TransactionRepository
from app.services.gemini_responses import (
    GeminiConfigurationError,
    GeminiExtractionError,
    GeminiResponsesClient,
)
from app.services.institutional_investors import InstitutionalInvestorRepository
from app.services.investment_advisors import InvestmentAdvisorRepository
from app.services.pdf_downloader import PdfDownloaderService, pdf_tempdir
from app.services.sec_pdf_fetcher import SecPdfFetchError, fetch_sec_pdf_bytes
from app.services.vault_retrieval import retrieve_chunks

logger = logging.getLogger(__name__)

# Caps. Kept as module constants so the schema-drift test and the tool
# descriptions can reference the same numbers.
PROFILE_FINANCIALS_LIMIT = 3
PROFILE_CLEARING_LIMIT = 3
SEARCH_RESULT_LIMIT_MAX = 10
SEARCH_RESULT_LIMIT_DEFAULT = 5
ADVISORY_LIST_CAP = 10
CLIENT_TYPE_LIST_CAP = 10
# list-by-filter tools get a slightly higher ceiling than the search tools
# because filters narrow the result space; returning 25 firms in CA matters
# more than returning 25 fuzzy name matches.
LIST_FILTER_LIMIT_DEFAULT = 10
LIST_FILTER_LIMIT_MAX = 25


@dataclass(frozen=True)
class Tool:
    """One tool that Gemini can call.

    ``execute`` takes the calling user (for ``ensure_feature``), an
    ``AsyncSession`` (the endpoint injects one per request), and the
    arg dict Gemini sent in the ``functionCall`` part. Returns the
    JSON-able result dict that becomes the ``functionResponse`` payload.

    Optional fields:

    - ``timeout_s`` — per-tool timeout that overrides the chat-service
      default (``TOOL_EXECUTION_TIMEOUT_S = 5s``). PDF-summarisation
      tools need more headroom: a 5 MB FOCUS report through inline
      base64 takes ~3-5s on its own, plus the Gemini summarisation
      round-trip. We give those tools 30s; the cheap repo-only tools
      keep the 5s default so a slow PDF can't poison the whole chat
      iteration budget.
    - ``cacheable`` — when False, the chat service skips writing the
      tool result into its per-process LRU. PDF summaries are long
      and per-question, so caching them gives a poor hit-rate while
      bloating the LRU's memory footprint.
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]
    feature_key: str
    execute: Callable[
        [AuthenticatedUser, AsyncSession, Mapping[str, Any]],
        Awaitable[dict[str, Any]],
    ]
    timeout_s: float | None = None
    cacheable: bool = True


# ── Helpers ──────────────────────────────────────────────────────────────


def _jsonable(value: Any) -> Any:
    """Recursively coerce Decimal / date / datetime to JSON-friendly types.

    httpx's default JSON encoder rejects ``Decimal`` and ``date`` — we'd
    rather pay a small projection cost here than discover that at runtime
    when Gemini's payload fails to serialize.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _check_feature(user: AuthenticatedUser, feature_key: str) -> dict[str, Any] | None:
    """Return an error dict if the user lacks the feature, else None."""
    try:
        ensure_feature(user, feature_key)
    except HTTPException as exc:
        if exc.status_code == 403:
            return {
                "error": "no_access",
                "message": (
                    f"User does not have access to the '{feature_key}' "
                    f"feature. Tell them they need that permission granted "
                    f"to use this lookup."
                ),
            }
        # Anything else from ensure_feature is unexpected — bubble it up to
        # the loop's generic catch-all rather than swallowing silently.
        raise
    return None


def _clamp_limit(raw: Any) -> int:
    try:
        n = int(raw) if raw is not None else SEARCH_RESULT_LIMIT_DEFAULT
    except (TypeError, ValueError):
        n = SEARCH_RESULT_LIMIT_DEFAULT
    return max(1, min(n, SEARCH_RESULT_LIMIT_MAX))


def _clamp_filter_limit(raw: Any) -> int:
    """Clamp helper for the list-by-filter tools (larger ceiling than search)."""
    try:
        n = int(raw) if raw is not None else LIST_FILTER_LIMIT_DEFAULT
    except (TypeError, ValueError):
        n = LIST_FILTER_LIMIT_DEFAULT
    return max(1, min(n, LIST_FILTER_LIMIT_MAX))


def _opt_str(args: Mapping[str, Any], key: str) -> str | None:
    """Return a trimmed string arg or None for missing / empty values."""
    raw = args.get(key)
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _opt_float(args: Mapping[str, Any], key: str) -> float | None:
    raw = args.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _opt_bool(args: Mapping[str, Any], key: str) -> bool | None:
    raw = args.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "yes", "y", "1"):
            return True
        if lowered in ("false", "no", "n", "0"):
            return False
    return None


def _require_query(args: Mapping[str, Any]) -> str | dict[str, Any]:
    """Return the trimmed query string or an ``error`` dict."""
    raw = args.get("query")
    query = str(raw).strip() if raw is not None else ""
    if not query:
        return {
            "error": "invalid_args",
            "message": "Argument 'query' is required and must be non-empty.",
        }
    return query


# ── Projections ──────────────────────────────────────────────────────────
#
# Keys returned here are referenced by ``test_chatbot_tools.py``'s schema-
# drift guard — every key in the projection must exist as a field on the
# underlying Pydantic schema (``BrokerDealerListItem`` / ``InvestmentAdvisorListItem``).
# If you rename a schema field, rename the projection key in lockstep.

_BD_SUMMARY_KEYS = (
    "id",
    "name",
    "crd_number",
    "cik",
    "city",
    "state",
    "status",
    "lead_priority",
    "current_clearing_partner",
    "latest_net_capital",
)


_BD_PROFILE_EXTRA_KEYS = (
    "three_year_cagr",
    "yoy_growth",
    "is_deficient",
    "registration_date",
    "last_filing_date",
    "latest_total_assets",
    "current_clearing_type",
    "health_status",
    "website",
)


_IA_SUMMARY_KEYS = (
    "id",
    "name",
    "crd_number",
    "cik",
    "city",
    "state",
    "status",
    "regulatory_aum",
    "files_13f",
    "total_clients",
)


_IA_PROFILE_EXTRA_KEYS = (
    "legal_name",
    "discretionary_aum",
    "non_discretionary_aum",
    "registration_date",
    "last_filing_date",
    "latest_13f_filing_date",
    "website",
)


_II_SUMMARY_KEYS = (
    "id",
    "name",
    "cik",
    "city",
    "state",
    "status",
    "total_aum",
    "holdings_count",
    "latest_13f_filing_date",
    "advisor_id",
)


_II_PROFILE_EXTRA_KEYS = (
    "legal_name",
    "website",
    "filings_index_url",
)


def _project_bd_summary(item: BrokerDealerListItem) -> dict[str, Any]:
    out: dict[str, Any] = {k: getattr(item, k, None) for k in _BD_SUMMARY_KEYS}
    # In-app deep link so Doxie can cite this firm with a clickable href.
    # The id is always present on a list-item shape; defend with a guard
    # just so a malformed test stub doesn't crash the whole projection.
    if out.get("id") is not None:
        out["link"] = bd_detail_url(out["id"])
    return _jsonable(out)


def _project_bd_profile(
    item: BrokerDealerListItem,
    financials: list[FinancialMetricItem],
    arrangements: list[ClearingArrangementItem],
) -> dict[str, Any]:
    out: dict[str, Any] = {k: getattr(item, k, None) for k in _BD_SUMMARY_KEYS}
    for k in _BD_PROFILE_EXTRA_KEYS:
        out[k] = getattr(item, k, None)
    if out.get("id") is not None:
        out["link"] = bd_detail_url(out["id"])
    out["latest_financials"] = [
        {
            "report_date": f.report_date,
            "net_capital": f.net_capital,
            "excess_net_capital": f.excess_net_capital,
            "total_assets": f.total_assets,
            "required_min_capital": f.required_min_capital,
        }
        for f in financials[:PROFILE_FINANCIALS_LIMIT]
    ]
    out["clearing_arrangements"] = [
        {
            "filing_year": a.filing_year,
            "report_date": a.report_date,
            "clearing_partner": a.clearing_partner,
            "clearing_type": a.clearing_type,
            "is_competitor": a.is_competitor,
        }
        for a in arrangements[:PROFILE_CLEARING_LIMIT]
    ]
    return _jsonable(out)


def _project_ia_summary(item: InvestmentAdvisorListItem) -> dict[str, Any]:
    out: dict[str, Any] = {k: getattr(item, k, None) for k in _IA_SUMMARY_KEYS}
    if out.get("id") is not None:
        out["link"] = ia_detail_url(out["id"])
    return _jsonable(out)


def _project_ia_profile(item: InvestmentAdvisorListItem) -> dict[str, Any]:
    out: dict[str, Any] = {k: getattr(item, k, None) for k in _IA_SUMMARY_KEYS}
    for k in _IA_PROFILE_EXTRA_KEYS:
        out[k] = getattr(item, k, None)
    if out.get("id") is not None:
        out["link"] = ia_detail_url(out["id"])
    # Cap multi-select arrays so a verbose ADV doesn't blow the prompt.
    activities = item.advisory_activities or []
    out["advisory_activities"] = list(activities[:ADVISORY_LIST_CAP])
    client_types = item.client_types or []
    out["client_types"] = list(client_types[:CLIENT_TYPE_LIST_CAP])
    return _jsonable(out)


def _project_ii_summary(item: InstitutionalInvestorListItem) -> dict[str, Any]:
    out: dict[str, Any] = {k: getattr(item, k, None) for k in _II_SUMMARY_KEYS}
    if out.get("id") is not None:
        out["link"] = ii_detail_url(out["id"])
    return _jsonable(out)


def _project_ii_profile(item: InstitutionalInvestorListItem) -> dict[str, Any]:
    out: dict[str, Any] = {k: getattr(item, k, None) for k in _II_SUMMARY_KEYS}
    for k in _II_PROFILE_EXTRA_KEYS:
        out[k] = getattr(item, k, None)
    if out.get("id") is not None:
        out["link"] = ii_detail_url(out["id"])
    return _jsonable(out)


# ── Tool execute functions ───────────────────────────────────────────────


_bd_repo = BrokerDealerRepository()
_ia_repo = InvestmentAdvisorRepository()
_ii_repo = InstitutionalInvestorRepository()
_form4_repo = Form4TransactionRepository()
_alerts_repo = AlertRepository()
_semantic_service = ChatbotSemanticService()
_pdf_downloader = PdfDownloaderService()
_gemini_client = GeminiResponsesClient()


# Per-tool timeout for PDF-summarisation tools. The chat service
# defaults to 5s for repo-only tools; PDF summaries need ~30s for
# fetch + Gemini round-trip (a 5MB FOCUS is ~3-5s on the inline path,
# plus the LLM call). Above 30s the user has likely abandoned the
# question anyway and we'd rather the model apologise gracefully than
# block the whole chat iteration budget.
PDF_TOOL_TIMEOUT_S = 30.0

# Vault RAG cap. Lower than retrieve_chunks' MAX_TOP_K so a chatty
# model can't ask for 20 chunks (which would blow the prompt budget
# when each chunk is ~500 tokens).
VAULT_TOP_K_MAX = 8
VAULT_TOP_K_DEFAULT = 5


# Form 4 / Investors tab — values mirror the FE's days preset whitelist
# and default. The repo accepts any positive int; we just cap for
# safety so a chatty model can't request a 10,000-day window.
FORM4_DAYS_DEFAULT = 90
FORM4_DAYS_MAX = 365
FORM4_RESULT_LIMIT_MAX = 10
FORM4_RESULT_LIMIT_DEFAULT = 5

# Alerts feed — small caps because the alerts page paginates and we
# just want a "what's new" snapshot in the chat.
ALERTS_RESULT_LIMIT_MAX = 15
ALERTS_RESULT_LIMIT_DEFAULT = 6

# Per-firm filing enumeration cap. Used by ``list_filings_for_firm``
# which is the precursor enumeration tool for the future PDF-summarize
# tools — 5 filings is a useful default that doesn't burn prompt tokens.
FILINGS_LIST_LIMIT_DEFAULT = 5
FILINGS_LIST_LIMIT_MAX = 25


# Semantic search returns at most this many hits regardless of what
# Gemini asks for. Higher caps balloon the prompt budget; lower caps
# make Doxie miss good matches that ranked just outside the window.
SEMANTIC_RESULT_LIMIT_MAX = 8
SEMANTIC_RESULT_LIMIT_DEFAULT = 5


async def _execute_search_broker_dealers(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial
    query_or_error = _require_query(args)
    if isinstance(query_or_error, dict):
        return query_or_error
    limit = _clamp_limit(args.get("limit"))
    try:
        response = await _bd_repo.list_broker_dealers(
            db,
            search=query_or_error,
            states=[],
            statuses=[],
            health_statuses=[],
            lead_priorities=[],
            clearing_partners=[],
            clearing_types=[],
            types_of_business=[],
            list_mode="all",
            sort_by="name",
            sort_dir="asc",
            page=1,
            limit=limit,
        )
    except Exception:
        logger.exception("doxie tool failed", extra={"tool": "search_broker_dealers"})
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    return {
        "items": [_project_bd_summary(item) for item in response.items],
        "total_matched": response.meta.total,
        # Deep-link the user into the same name-filtered list. The FE
        # reads ``q`` and shows all matches; useful for the model to
        # cite when the search returns more rows than we capped.
        "list_link": bd_list_url(q=query_or_error),
    }


async def _execute_get_broker_dealer_profile(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial
    try:
        bd_id = int(args.get("broker_dealer_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": "Argument 'broker_dealer_id' must be an integer.",
        }
    try:
        bd = await _bd_repo.get_broker_dealer(db, bd_id)
        if bd is None:
            return {
                "error": "not_found",
                "message": f"No broker-dealer with id={bd_id}.",
            }
        financials = await _bd_repo.get_financial_metrics(db, bd_id)
        arrangements = await _bd_repo.list_clearing_arrangements(db, bd_id)
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "get_broker_dealer_profile"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    # ``from_attributes=True`` on each schema lets us validate the ORM
    # instances directly into Pydantic shapes for projection.
    bd_item = BrokerDealerListItem.model_validate(bd)
    financial_items = [FinancialMetricItem.model_validate(f) for f in financials]
    arrangement_items = [
        ClearingArrangementItem.model_validate(a) for a in arrangements
    ]
    return _project_bd_profile(bd_item, financial_items, arrangement_items)


async def _execute_search_investment_advisors(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, INVESTMENT_ADVISORS)
    if denial is not None:
        return denial
    query_or_error = _require_query(args)
    if isinstance(query_or_error, dict):
        return query_or_error
    limit = _clamp_limit(args.get("limit"))
    try:
        response = await _ia_repo.list_investment_advisors(
            db,
            search=query_or_error,
            states=[],
            statuses=[],
            advisory_activities=[],
            client_types=[],
            # ``files_13f=None`` disables the hard 13F scope the master-list
            # endpoint defaults on — Doxie shouldn't silently hide non-13F
            # advisors from a name search.
            files_13f=None,
            min_regulatory_aum=None,
            max_regulatory_aum=None,
            registered_after=None,
            registered_before=None,
            sort_by="name",
            sort_dir="asc",
            page=1,
            limit=limit,
        )
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "search_investment_advisors"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    return {
        "items": [_project_ia_summary(item) for item in response.items],
        "total_matched": response.meta.total,
        # The advisor search above disabled the hard 13F default
        # (files_13f=None), so mirror that on the link by sending
        # ``files_13f=all`` — otherwise the FE re-applies the 13F-only
        # scope and the user sees fewer rows than the tool returned.
        "list_link": ia_list_url(q=query_or_error, files_13f="all"),
    }


async def _execute_get_investment_advisor_profile(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, INVESTMENT_ADVISORS)
    if denial is not None:
        return denial
    try:
        advisor_id = int(args.get("advisor_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": "Argument 'advisor_id' must be an integer.",
        }
    try:
        advisor = await _ia_repo.get_investment_advisor(db, advisor_id)
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "get_investment_advisor_profile"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    if advisor is None:
        return {
            "error": "not_found",
            "message": f"No investment advisor with id={advisor_id}.",
        }
    item = InvestmentAdvisorListItem.model_validate(advisor)
    return _project_ia_profile(item)


async def _execute_search_institutional_investors(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, INSTITUTIONAL_INVESTORS)
    if denial is not None:
        return denial
    query_or_error = _require_query(args)
    if isinstance(query_or_error, dict):
        return query_or_error
    limit = _clamp_limit(args.get("limit"))
    try:
        response = await _ii_repo.list_institutional_investors(
            db,
            search=query_or_error,
            states=[],
            statuses=[],
            min_total_aum=None,
            max_total_aum=None,
            filed_13f_after=None,
            filed_13f_before=None,
            only_with_advisor_link=None,
            sort_by="name",
            sort_dir="asc",
            page=1,
            limit=limit,
        )
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "search_institutional_investors"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    # No ``list_link`` for IIs — the institutional-investor list page was
    # dropped in #438 (sidebar tab gone). Each item still carries its own
    # ``link`` to the detail page via _project_ii_summary.
    return {
        "items": [_project_ii_summary(item) for item in response.items],
        "total_matched": response.meta.total,
    }


async def _execute_get_institutional_investor_profile(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, INSTITUTIONAL_INVESTORS)
    if denial is not None:
        return denial
    try:
        investor_id = int(args.get("investor_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": "Argument 'investor_id' must be an integer.",
        }
    try:
        investor = await _ii_repo.get_institutional_investor(db, investor_id)
    except Exception:
        logger.exception(
            "doxie tool failed",
            extra={"tool": "get_institutional_investor_profile"},
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    if investor is None:
        return {
            "error": "not_found",
            "message": f"No institutional investor with id={investor_id}.",
        }
    item = InstitutionalInvestorListItem.model_validate(investor)
    return _project_ii_profile(item)


async def _execute_list_broker_dealers_by_filter(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Criteria-style BD list (state, status, clearing partner, etc.).

    Distinct from ``search_broker_dealers`` (which is fuzzy-name-match):
    this tool takes structured filters and returns the firms that satisfy
    *all* of them. At least one filter must be set — an unfiltered query
    is just a name-less search, and Gemini should call the search tool
    for that.
    """
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial

    state = _opt_str(args, "state")
    status = _opt_str(args, "status")
    clearing_partner = _opt_str(args, "clearing_partner")
    lead_priority = _opt_str(args, "lead_priority")
    min_net_capital = _opt_float(args, "min_net_capital")
    max_net_capital = _opt_float(args, "max_net_capital")

    any_filter_set = any(
        v is not None
        for v in (
            state,
            status,
            clearing_partner,
            lead_priority,
            min_net_capital,
            max_net_capital,
        )
    )
    if not any_filter_set:
        return {
            "error": "invalid_args",
            "message": (
                "At least one filter is required. Use search_broker_dealers "
                "for fuzzy name lookups instead."
            ),
        }

    limit = _clamp_filter_limit(args.get("limit"))
    try:
        response = await _bd_repo.list_broker_dealers(
            db,
            search=None,
            states=[state] if state else [],
            statuses=[status] if status else [],
            health_statuses=[],
            lead_priorities=[lead_priority] if lead_priority else [],
            clearing_partners=[clearing_partner] if clearing_partner else [],
            clearing_types=[],
            types_of_business=[],
            list_mode="all",
            sort_by="latest_net_capital",
            sort_dir="desc",
            page=1,
            limit=limit,
            min_net_capital=min_net_capital,
            max_net_capital=max_net_capital,
        )
    except Exception:
        logger.exception(
            "doxie tool failed",
            extra={"tool": "list_broker_dealers_by_filter"},
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    return {
        "items": [_project_bd_summary(item) for item in response.items],
        "total_matched": response.meta.total,
        # Echo the same filters into the link so the user lands on a
        # pre-filtered master list mirroring what the tool returned.
        "list_link": bd_list_url(
            state=state,
            lead_priority=lead_priority,
            clearing_partner=[clearing_partner] if clearing_partner else None,
            min_net_capital=min_net_capital,
            max_net_capital=max_net_capital,
        ),
    }


async def _execute_semantic_firm_search(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Conceptual / semantic firm lookup via the RAG embedding index.

    Distinct from ``search_broker_dealers`` (substring on name/CRD/CIK).
    Use when the user's query is descriptive — "broker-dealers
    specializing in retail HNW clients", "firms similar to Acme" —
    rather than a specific identifier or name fragment.
    """
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial
    query_or_error = _require_query(args)
    if isinstance(query_or_error, dict):
        return query_or_error
    try:
        raw_limit = int(args["limit"]) if args.get("limit") is not None else SEMANTIC_RESULT_LIMIT_DEFAULT
    except (TypeError, ValueError):
        raw_limit = SEMANTIC_RESULT_LIMIT_DEFAULT
    limit = max(1, min(raw_limit, SEMANTIC_RESULT_LIMIT_MAX))

    try:
        hits = await _semantic_service.search(
            db,
            query=query_or_error,
            entity_types=[ENTITY_TYPE_BROKER_DEALER],
            limit=limit,
        )
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "semantic_firm_search"}
        )
        return {
            "error": "tool_error",
            "message": (
                "Semantic search failed; the index may not be populated yet. "
                "Ask the user to try a name-based search instead."
            ),
        }

    if not hits:
        return {
            "items": [],
            "total_matched": 0,
            "note": (
                "No semantic matches found. The embedding index may not be "
                "populated; an admin can backfill it from settings."
            ),
        }

    # Fetch the BD rows for each hit so Doxie has names + identifiers to
    # cite. The hit's ``content`` snippet is what the embedding actually
    # matched — surfacing it lets Doxie quote the relevant phrase.
    bd_ids = [h.entity_id for h in hits if h.entity_type == ENTITY_TYPE_BROKER_DEALER]
    items: list[dict[str, Any]] = []
    for hit in hits:
        if hit.entity_type != ENTITY_TYPE_BROKER_DEALER:
            continue
        try:
            bd = await _bd_repo.get_broker_dealer(db, hit.entity_id)
        except Exception:
            logger.exception(
                "doxie tool failed loading bd id=%s tool=semantic_firm_search",
                hit.entity_id,
            )
            continue
        if bd is None:
            # Embedding row points at a deleted BD — stale index entry.
            # Skip silently; a re-backfill will clean it up.
            continue
        bd_item = BrokerDealerListItem.model_validate(bd)
        summary = _project_bd_summary(bd_item)
        summary["similarity"] = round(hit.similarity, 4)
        summary["match_snippet"] = hit.content[:200]
        items.append(summary)

    return {
        "items": items,
        "total_matched": len(items),
        # The total we *would have* returned if not capped by feature
        # gates or stale-row filtering. Helpful for Doxie to mention
        # when the result set is truncated.
        "candidates_considered": len(bd_ids),
        # Pass the user's natural-language query through as a name search
        # on the master list. Won't always match what the embedding
        # matched on, but it's the most useful fallback link we can offer.
        "list_link": bd_list_url(q=query_or_error),
    }


async def _execute_list_investment_advisors_by_filter(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Criteria-style IA list (state, status, AUM band, 13F flag).

    Distinct from ``search_investment_advisors``. Requires at least one
    filter so Gemini doesn't burn tokens enumerating a whole table.
    """
    denial = _check_feature(user, INVESTMENT_ADVISORS)
    if denial is not None:
        return denial

    state = _opt_str(args, "state")
    status = _opt_str(args, "status")
    min_regulatory_aum = _opt_float(args, "min_regulatory_aum")
    max_regulatory_aum = _opt_float(args, "max_regulatory_aum")
    files_13f = _opt_bool(args, "files_13f")

    any_filter_set = any(
        v is not None
        for v in (state, status, min_regulatory_aum, max_regulatory_aum, files_13f)
    )
    if not any_filter_set:
        return {
            "error": "invalid_args",
            "message": (
                "At least one filter is required. Use search_investment_advisors "
                "for fuzzy name lookups instead."
            ),
        }

    limit = _clamp_filter_limit(args.get("limit"))
    try:
        response = await _ia_repo.list_investment_advisors(
            db,
            search=None,
            states=[state] if state else [],
            statuses=[status] if status else [],
            advisory_activities=[],
            client_types=[],
            files_13f=files_13f,
            min_regulatory_aum=min_regulatory_aum,
            max_regulatory_aum=max_regulatory_aum,
            registered_after=None,
            registered_before=None,
            sort_by="regulatory_aum",
            sort_dir="desc",
            page=1,
            limit=limit,
        )
    except Exception:
        logger.exception(
            "doxie tool failed",
            extra={"tool": "list_investment_advisors_by_filter"},
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    return {
        "items": [_project_ia_summary(item) for item in response.items],
        "total_matched": response.meta.total,
        "list_link": ia_list_url(
            state=state,
            status=status if status != "All" else None,
            files_13f=files_13f,
            min_regulatory_aum=min_regulatory_aum,
            max_regulatory_aum=max_regulatory_aum,
        ),
    }


# ── Form 4 (Investors tab) ────────────────────────────────────────────────


# Maps the BE `ad_code` (A/D) to the FE `/investors?tab=` param. The FE
# tab enum is buyers (acquired) / sellers (disposed) / all — Doxie's
# tool surface uses ad_code directly because that's the column name in
# the DB and the more natural concept for the model to reason about.
_AD_CODE_TO_TAB: dict[str, str] = {"A": "buyers", "D": "sellers"}


def _project_form4_consolidated(row: Any) -> dict[str, Any]:
    """Compact projection of one ``ConsolidatedPersonRow``.

    The full dataclass has 27 fields including raw address parts and
    favorites flags; Doxie doesn't need any of that. We keep the insider
    identity, the issuer they traded against, the aggregate shares/value,
    and a single ``link`` into the /investors workspace pre-filtered by
    ticker so the user can drill in.
    """
    ticker = getattr(row, "issuer_ticker", None)
    ad_code = getattr(row, "ad_code", None)
    out: dict[str, Any] = {
        "reporting_owner_name": getattr(row, "reporting_owner_name", None),
        "reporting_owner_cik": getattr(row, "reporting_owner_cik", None),
        "reporting_owner_title": getattr(row, "reporting_owner_title", None),
        "reporting_owner_state": getattr(row, "reporting_owner_state", None),
        "is_director": getattr(row, "reporting_owner_is_director", None),
        "is_officer": getattr(row, "reporting_owner_is_officer", None),
        "is_ten_pct": getattr(row, "reporting_owner_is_ten_pct", None),
        "issuer_name": getattr(row, "issuer_name", None),
        "issuer_ticker": ticker,
        "ad_code": ad_code,
        # Human-readable bucket so Doxie doesn't have to remember the A/D
        # convention. ``acquired`` = insider bought; ``disposed`` = sold.
        "transaction_kind": "acquired" if ad_code == "A" else "disposed" if ad_code == "D" else None,
        "security_title": getattr(row, "security_title", None),
        "transaction_date": getattr(row, "transaction_date", None),
        "shares": getattr(row, "shares", None),
        "transaction_value": getattr(row, "transaction_value", None),
        "txn_count": getattr(row, "txn_count", None),
        "filed_at": getattr(row, "filed_at", None),
        "source_filing_url": getattr(row, "source_filing_url", None),
    }
    # Deep-link into /investors pre-filtered. ``ticker`` is the most
    # useful narrowing — drops to the relevant issuer; ``tab`` maps from
    # ad_code. When neither is present we still send the user to the
    # bare /investors page.
    out["link"] = investors_url(
        ticker=ticker,
        tab=_AD_CODE_TO_TAB.get(ad_code) if ad_code else None,
    )
    return _jsonable(out)


async def _execute_search_form4_filings(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Search recent Form 4 insider filings.

    Takes any combination of free-text query, A/D bucket, ticker, days-
    back window, and min transaction value. At least one of these must
    be set so a chatty model can't enumerate the whole table — the BD /
    IA tools follow the same "filter required" rule.

    Note: The original plan called for a separate ``get_form4_investor_profile``
    tool but the Form 4 schema has no per-insider entity (each row is a
    transaction, not an investor profile). A search with ``query=<name>``
    covers the same use case.
    """
    denial = _check_feature(user, INVESTORS)
    if denial is not None:
        return denial

    query = _opt_str(args, "query")
    ticker = _opt_str(args, "ticker")
    ad_code_raw = _opt_str(args, "ad_code")
    ad_code: str | None = None
    if ad_code_raw:
        upper = ad_code_raw.upper()
        if upper in ("A", "D"):
            ad_code = upper
        else:
            return {
                "error": "invalid_args",
                "message": "Argument 'ad_code' must be 'A' (acquired/buy) or 'D' (disposed/sell).",
            }

    min_value = _opt_float(args, "min_value")
    raw_days = args.get("days")
    try:
        days = int(raw_days) if raw_days is not None else FORM4_DAYS_DEFAULT
    except (TypeError, ValueError):
        days = FORM4_DAYS_DEFAULT
    days = max(1, min(days, FORM4_DAYS_MAX))

    if query is None and ticker is None and ad_code is None and min_value is None:
        return {
            "error": "invalid_args",
            "message": (
                "At least one of 'query', 'ticker', 'ad_code', or 'min_value' "
                "must be set. Use ad_code='A' for insider buys, 'D' for sells."
            ),
        }

    try:
        raw_limit = int(args["limit"]) if args.get("limit") is not None else FORM4_RESULT_LIMIT_DEFAULT
    except (TypeError, ValueError):
        raw_limit = FORM4_RESULT_LIMIT_DEFAULT
    limit = max(1, min(raw_limit, FORM4_RESULT_LIMIT_MAX))

    try:
        from decimal import Decimal as _Decimal

        rows, total = await _form4_repo.list_consolidated_persons(
            db,
            ad_code=ad_code,  # type: ignore[arg-type]
            ticker=ticker,
            days=days,
            min_value=_Decimal(str(min_value)) if min_value is not None else None,
            max_value=None,
            search=query,
            states=None,
            sort_by="transaction_date",
            sort_dir="desc",
            page=1,
            limit=limit,
            user_id=None,
        )
    except Exception:
        logger.exception("doxie tool failed", extra={"tool": "search_form4_filings"})
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }

    return {
        "items": [_project_form4_consolidated(r) for r in rows],
        "total_matched": total,
        # ``list_link`` echoes the filters so the user lands on the same
        # cut of the /investors workspace.
        "list_link": investors_url(
            tab=_AD_CODE_TO_TAB.get(ad_code) if ad_code else None,
            q=query,
            ticker=ticker,
            days=days if days != FORM4_DAYS_DEFAULT else None,
            min_value=min_value,
        ),
    }


# ── Per-firm filing enumeration ───────────────────────────────────────────


def _project_filing(row: Any, *, firm_type: str, firm_id: int) -> dict[str, Any]:
    """Compact projection of one filing alert / advisor / investor filing.

    All three filing tables share the same shape (id, form_type, filed_at,
    summary, source_filing_url) so one projection serves them all. The
    ``link`` is a deep-link to the parent firm's detail page — Part C will
    add a separate ``summarize_*_filing`` tool that returns the PDF
    summary itself, and the ``filing_id`` here is the handle that tool
    accepts to identify which filing to fetch.
    """
    out: dict[str, Any] = {
        "filing_id": getattr(row, "id", None),
        "form_type": getattr(row, "form_type", None),
        "filed_at": getattr(row, "filed_at", None),
        "summary": getattr(row, "summary", None),
        "source_filing_url": getattr(row, "source_filing_url", None),
        "priority": getattr(row, "priority", None),
    }
    if firm_type == "bd":
        out["link"] = bd_detail_url(firm_id)
    elif firm_type == "ia":
        out["link"] = ia_detail_url(firm_id)
    elif firm_type == "ii":
        out["link"] = ii_detail_url(firm_id)
    return _jsonable(out)


async def _execute_list_filings_for_firm(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """List recent filings attached to a BD / IA / II.

    Wraps three repository methods behind one tool so the model can ask
    "what filings does Apex Clearing have?" without having to know which
    table to look in. The shape returned is identical across firm types
    so Doxie can format the result uniformly.

    Part C uses this as the enumeration step before opening one PDF: the
    ``filing_id`` returned here is the handle the ``summarize_*_filing``
    tools accept.
    """
    firm_type_raw = _opt_str(args, "firm_type")
    if not firm_type_raw:
        return {
            "error": "invalid_args",
            "message": "Argument 'firm_type' must be 'bd', 'ia', or 'ii'.",
        }
    firm_type = firm_type_raw.lower()
    if firm_type not in ("bd", "ia", "ii"):
        return {
            "error": "invalid_args",
            "message": "Argument 'firm_type' must be 'bd', 'ia', or 'ii'.",
        }

    # Each firm type is gated on its own feature key — keeps Doxie from
    # leaking even filing metadata to a user who shouldn't see the firm.
    feature_key = {
        "bd": MASTER_LIST,
        "ia": INVESTMENT_ADVISORS,
        "ii": INSTITUTIONAL_INVESTORS,
    }[firm_type]
    denial = _check_feature(user, feature_key)
    if denial is not None:
        return denial

    try:
        firm_id = int(args.get("firm_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": "Argument 'firm_id' must be an integer.",
        }

    form_type_filter = _opt_str(args, "form_type")
    try:
        raw_limit = int(args["limit"]) if args.get("limit") is not None else FILINGS_LIST_LIMIT_DEFAULT
    except (TypeError, ValueError):
        raw_limit = FILINGS_LIST_LIMIT_DEFAULT
    limit = max(1, min(raw_limit, FILINGS_LIST_LIMIT_MAX))

    try:
        if firm_type == "bd":
            # The BD filing-history repo doesn't accept a limit — slice
            # client-side. ``get_filing_history`` returns filings sorted
            # by filed_at desc, so the head is the most recent.
            all_filings = await _alerts_repo.get_filing_history(db, firm_id)
            filings = list(all_filings)
        elif firm_type == "ia":
            filings = list(await _ia_repo.list_advisor_filings(db, firm_id, limit=limit))
        else:  # ii
            filings = list(await _ii_repo.list_investor_filings(db, firm_id, limit=limit))
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "list_filings_for_firm"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }

    if form_type_filter:
        filings = [f for f in filings if getattr(f, "form_type", None) == form_type_filter]

    items = [
        _project_filing(f, firm_type=firm_type, firm_id=firm_id)
        for f in filings[:limit]
    ]
    return {
        "items": items,
        "total_matched": len(items),
    }


# ── Filing alerts ─────────────────────────────────────────────────────────


def _project_alert(item: Any) -> dict[str, Any]:
    """Compact projection of one ``AlertListItem``."""
    bd_id = getattr(item, "bd_id", None)
    out: dict[str, Any] = {
        "id": getattr(item, "id", None),
        "bd_id": bd_id,
        "firm_name": getattr(item, "firm_name", None),
        "form_type": getattr(item, "form_type", None),
        "priority": getattr(item, "priority", None),
        "filed_at": getattr(item, "filed_at", None),
        "summary": getattr(item, "summary", None),
        "source_filing_url": getattr(item, "source_filing_url", None),
        "is_read": getattr(item, "is_read", None),
    }
    # Deep-link to the firm whose filing triggered the alert. Sending
    # the user to a per-alert page would also be fine, but the firm
    # detail is the more useful destination ("see what Apex filed").
    if bd_id is not None:
        out["link"] = bd_detail_url(bd_id)
    return _jsonable(out)


async def _execute_get_recent_alerts(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Recent filing alerts feed.

    Useful for "what's new?" prompts — "any new Form X-17A-5 filings
    this week?" Gated on the ``alerts`` feature so a viewer without
    that page can't pull a global feed through Doxie.

    Filtering knobs mirror what the /alerts page surfaces: form_type,
    priority, optional broker_dealer_id, and is_read. All optional.
    """
    denial = _check_feature(user, ALERTS)
    if denial is not None:
        return denial

    form_type = _opt_str(args, "form_type")
    priority = _opt_str(args, "priority")
    bd_id_raw = args.get("broker_dealer_id")
    bd_id: int | None = None
    if bd_id_raw is not None:
        try:
            bd_id = int(bd_id_raw)
        except (TypeError, ValueError):
            return {
                "error": "invalid_args",
                "message": "Argument 'broker_dealer_id' must be an integer.",
            }

    is_read = _opt_bool(args, "is_read")

    try:
        raw_limit = int(args["limit"]) if args.get("limit") is not None else ALERTS_RESULT_LIMIT_DEFAULT
    except (TypeError, ValueError):
        raw_limit = ALERTS_RESULT_LIMIT_DEFAULT
    limit = max(1, min(raw_limit, ALERTS_RESULT_LIMIT_MAX))

    try:
        response = await _alerts_repo.list_alerts(
            db,
            form_types=[form_type] if form_type else [],
            priorities=[priority] if priority else [],
            is_read=is_read,
            broker_dealer_id=bd_id,
            page=1,
            limit=limit,
        )
    except Exception:
        logger.exception("doxie tool failed", extra={"tool": "get_recent_alerts"})
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }

    return {
        "items": [_project_alert(item) for item in response.items],
        "total_matched": response.meta.total,
        # The alerts page itself has no URL-state for form_type filter
        # (the route only takes params at route-mount time, not synced
        # via useUrlSyncedState) — so the link is the bare alerts URL.
        "list_link": ALERTS_URL,
    }


# ── PDF summarisation tools ──────────────────────────────────────────────


def _build_filing_summary_prompt(
    *,
    filing_label: str,
    firm_name: str | None,
    question: str | None,
) -> str:
    """Compose the user prompt for ``summarize_pdf``.

    Generic shape used by every BD / IA / II / Form 4 summariser. The
    filing-specific knobs are which form_type label to use and which
    firm name to anchor the answer in — both passed in by the caller.
    The optional ``question`` lets the model focus the summary on what
    the user actually asked, while still grounding everything in the
    document so it can't drift into hallucination.
    """
    parts: list[str] = [
        f"Summarize this {filing_label}",
    ]
    if firm_name:
        parts[-1] += f" for {firm_name}"
    parts[-1] += "."
    parts.append(
        "Focus on: registration / filing data, financial highlights "
        "(net capital, AUM, total assets where present), counterparty "
        "relationships (clearing partners for BDs, top holdings for "
        "13F filers), and any deficiencies or material changes flagged "
        "in the filing."
    )
    if question:
        parts.append(
            "The user specifically asked: "
            f"{question}\nAddress this directly in your summary if "
            "the filing contains relevant information; if it does not, "
            "say so."
        )
    parts.append(
        "Use 1-3 short paragraphs. Cite specific numbers, dates, and "
        "names exactly as they appear in the document. Never invent "
        "values that are not present."
    )
    return "\n\n".join(parts)


async def _run_pdf_summary(
    *,
    pdf_bytes: bytes,
    prompt: str,
    source_filing_url: str | None,
    link: str | None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared PDF → Gemini summary → result-dict path.

    All five PDF tools end up here once they've resolved the bytes for
    one document. Keeps the Gemini error handling (config / extraction /
    zero-page → None) in one place.
    """
    pdf_bytes_b64 = base64.b64encode(pdf_bytes).decode("ascii")
    try:
        summary = await _gemini_client.summarize_pdf(
            pdf_bytes_base64=pdf_bytes_b64, prompt=prompt
        )
    except GeminiConfigurationError:
        logger.warning("doxie pdf summary unavailable — gemini key missing")
        return {
            "error": "tool_error",
            "message": (
                "PDF summarisation isn't configured on this environment. "
                "Apologise and offer to surface the source filing URL "
                "instead."
            ),
            "source_filing_url": source_filing_url,
        }
    except GeminiExtractionError as exc:
        logger.warning("doxie pdf summary failed: %s", exc)
        return {
            "error": "tool_error",
            "message": (
                "Couldn't summarise this filing. Apologise to the user "
                "and offer the source filing URL as a fallback."
            ),
            "source_filing_url": source_filing_url,
        }

    if summary is None:
        # ``summarize_pdf`` returns None for known-unparseable PDFs (e.g.
        # zero-page X-17A-5 artifacts). Surface a friendly error so the
        # model relays it instead of giving the user empty text.
        return {
            "error": "unparseable",
            "message": (
                "This filing appears to be empty or unparseable. Apologise "
                "and offer the source URL so the user can view it directly."
            ),
            "source_filing_url": source_filing_url,
        }

    out: dict[str, Any] = {
        "summary": summary,
        "source_filing_url": source_filing_url,
    }
    if link is not None:
        out["link"] = link
    if extras:
        out.update(extras)
    return out


async def _execute_summarize_broker_dealer_filing(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarise a broker-dealer's most recent Form X-17A-5 (FOCUS) filing.

    Uses the existing ``PdfDownloaderService.download_latest_x17a5_pdf``
    pipeline — same SEC SSRF allowlist + size ceiling + multi-document
    Statement-of-Financial-Condition picker as the financial-extraction
    cron. The result is then fed to ``GeminiResponsesClient.summarize_pdf``
    in prose mode so the user gets a narrative summary rather than the
    structured JSON the cron produces.

    The plan called for an optional ``filing_id`` arg to pick a specific
    historical filing; that path needs additional resolver code beyond
    Part C's scope, so for now this always returns the latest X-17A-5.
    The model can call ``list_filings_for_firm`` to enumerate older
    filings and surface their ``source_filing_url`` directly.
    """
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial

    try:
        bd_id = int(args.get("broker_dealer_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": "Argument 'broker_dealer_id' must be an integer.",
        }

    question = _opt_str(args, "question")

    try:
        bd = await _bd_repo.get_broker_dealer(db, bd_id)
    except Exception:
        logger.exception(
            "doxie tool failed",
            extra={"tool": "summarize_broker_dealer_filing"},
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    if bd is None:
        return {
            "error": "not_found",
            "message": f"No broker-dealer with id={bd_id}.",
        }

    try:
        with pdf_tempdir() as dest_dir:
            record = await _pdf_downloader.download_latest_x17a5_pdf(bd, dest_dir)
            if record is None:
                return {
                    "error": "not_found",
                    "message": (
                        "No Form X-17A-5 / FOCUS filing is available on "
                        "EDGAR for this broker-dealer."
                    ),
                    "link": bd_detail_url(bd_id),
                }
            # ``bytes_base64`` is populated on the legacy non-Files-API
            # path (the default in production); the Files-API path leaves
            # it empty and writes to ``local_document_path``. Handle both.
            if record.bytes_base64:
                pdf_bytes = base64.b64decode(record.bytes_base64)
            else:
                pdf_bytes = Path(record.local_document_path).read_bytes()
    except Exception:
        logger.exception(
            "doxie tool failed",
            extra={"tool": "summarize_broker_dealer_filing"},
        )
        return {
            "error": "tool_error",
            "message": (
                "Couldn't fetch the FOCUS PDF from SEC EDGAR. Apologise "
                "and offer the firm detail link as a fallback."
            ),
            "link": bd_detail_url(bd_id),
        }

    firm_name = getattr(bd, "name", None)
    prompt = _build_filing_summary_prompt(
        filing_label="Form X-17A-5 (FOCUS) annual broker-dealer filing",
        firm_name=firm_name,
        question=question,
    )
    return await _run_pdf_summary(
        pdf_bytes=pdf_bytes,
        prompt=prompt,
        source_filing_url=record.source_filing_url,
        link=bd_detail_url(bd_id),
        extras={
            "firm_name": firm_name,
            "filing_year": record.filing_year,
            "report_date": (
                record.report_date.isoformat() if record.report_date else None
            ),
        },
    )


async def _execute_summarize_brokercheck_pdf(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarise the FINRA BrokerCheck Detailed Report PDF for a BD.

    Uses ``finra_pdf_service.fetch_brokercheck_pdf`` which hits the
    deterministic ``files.brokercheck.finra.org/firm/firm_{crd}.pdf``
    URL (no SEC). Different doc family from X-17A-5 — BrokerCheck
    covers disclosures, branch counts, employment history, customer
    complaints, regulatory actions; X-17A-5 is the audited financials.
    """
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial

    try:
        bd_id = int(args.get("broker_dealer_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": "Argument 'broker_dealer_id' must be an integer.",
        }

    question = _opt_str(args, "question")

    try:
        bd = await _bd_repo.get_broker_dealer(db, bd_id)
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "summarize_brokercheck_pdf"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    if bd is None:
        return {
            "error": "not_found",
            "message": f"No broker-dealer with id={bd_id}.",
        }
    crd = getattr(bd, "crd_number", None)
    if not crd:
        return {
            "error": "not_found",
            "message": (
                "This broker-dealer has no CRD on file; BrokerCheck PDFs "
                "are keyed on CRD. Try a different identifier."
            ),
            "link": bd_detail_url(bd_id),
        }

    try:
        pdf_bytes = await fetch_brokercheck_pdf(crd)
    except FinraPdfNotFound:
        return {
            "error": "not_found",
            "message": (
                f"FINRA has no BrokerCheck Detailed Report PDF for CRD "
                f"{crd}. The firm may not be currently registered."
            ),
            "link": bd_detail_url(bd_id),
        }
    except FinraPdfFetchError as exc:
        logger.warning("doxie brokercheck fetch failed: %s", exc)
        return {
            "error": "tool_error",
            "message": (
                "Couldn't fetch the BrokerCheck PDF from FINRA. Apologise "
                "and offer the firm detail link as a fallback."
            ),
            "link": bd_detail_url(bd_id),
        }

    firm_name = getattr(bd, "name", None)
    source_url = f"https://brokercheck.finra.org/firm/summary/{crd}"
    prompt = _build_filing_summary_prompt(
        filing_label="FINRA BrokerCheck Detailed Report",
        firm_name=firm_name,
        question=question,
    )
    return await _run_pdf_summary(
        pdf_bytes=pdf_bytes,
        prompt=prompt,
        source_filing_url=source_url,
        link=bd_detail_url(bd_id),
        extras={"firm_name": firm_name, "crd_number": crd},
    )


async def _summarise_filing_by_url(
    *,
    user: AuthenticatedUser,
    db: AsyncSession,
    feature_key: str,
    fetch_filing: Callable[[AsyncSession], Awaitable[Any]],
    filing_label: str,
    firm_name: str | None,
    detail_link: str | None,
    question: str | None,
    tool_name: str,
) -> dict[str, Any]:
    """Shared implementation for the IA / II / Form 4 PDF tools.

    Each of those tools fronts a different table but the flow is
    identical: load the filing row, validate ``source_filing_url``
    against the SEC allowlist, fetch the bytes, summarise. The caller
    supplies the table-specific fetcher and labels.
    """
    denial = _check_feature(user, feature_key)
    if denial is not None:
        return denial

    try:
        filing = await fetch_filing(db)
    except Exception:
        logger.exception("doxie tool failed", extra={"tool": tool_name})
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    if filing is None:
        return {
            "error": "not_found",
            "message": "No matching filing found in the database.",
            "link": detail_link,
        }

    source_url = getattr(filing, "source_filing_url", None)
    if not source_url:
        return {
            "error": "not_found",
            "message": (
                "This filing has no source URL on file, so the PDF can't "
                "be fetched. Apologise to the user."
            ),
            "link": detail_link,
        }

    try:
        pdf_bytes = await fetch_sec_pdf_bytes(source_url)
    except SecPdfFetchError as exc:
        logger.warning("doxie sec pdf fetch failed: %s", exc)
        # Surface as a clean error with the URL so the model can offer
        # the link instead. The HTML-index case (most common for EDGAR
        # filing URLs) gets a specific message too.
        return {
            "error": "tool_error",
            "message": (
                f"Couldn't fetch the filing PDF: {exc}. Offer the source "
                f"URL to the user as a fallback."
            ),
            "source_filing_url": source_url,
            "link": detail_link,
        }

    prompt = _build_filing_summary_prompt(
        filing_label=filing_label,
        firm_name=firm_name,
        question=question,
    )
    return await _run_pdf_summary(
        pdf_bytes=pdf_bytes,
        prompt=prompt,
        source_filing_url=source_url,
        link=detail_link,
        extras={
            "firm_name": firm_name,
            "form_type": getattr(filing, "form_type", None),
            "filed_at": (
                getattr(filing, "filed_at", None).isoformat()
                if getattr(filing, "filed_at", None)
                else None
            ),
        },
    )


async def _execute_summarize_investment_advisor_filing(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarise an investment advisor's Form ADV / 13F-HR filing.

    Requires the filing_id Doxie pulled from ``list_filings_for_firm``.
    The advisor_filings table has a ``source_filing_url`` per row; the
    SEC PDF fetcher validates and downloads it."""
    try:
        filing_id = int(args.get("filing_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": (
                "Argument 'filing_id' must be an integer. Get it from "
                "list_filings_for_firm with firm_type='ia'."
            ),
        }

    question = _opt_str(args, "question")

    async def fetcher(session: AsyncSession) -> Any:
        return (
            await session.execute(
                select(AdvisorFiling).where(AdvisorFiling.id == filing_id)
            )
        ).scalar_one_or_none()

    # Pre-resolve the advisor name so the prompt is anchored to a firm.
    # ``filing.advisor_id`` only loads inside the fetcher above, so do
    # a single small lookup here to keep the helper firm-agnostic.
    filing = await fetcher(db)
    if filing is None:
        return {
            "error": "not_found",
            "message": f"No advisor filing with id={filing_id}.",
        }
    advisor_id = getattr(filing, "advisor_id", None)
    firm_name = None
    detail_link = None
    if advisor_id is not None:
        try:
            advisor = await _ia_repo.get_investment_advisor(db, advisor_id)
            if advisor is not None:
                firm_name = getattr(advisor, "name", None)
                detail_link = ia_detail_url(advisor_id)
        except Exception:
            logger.exception("doxie tool failed loading advisor for filing")
            # Continue without firm_name — the summary is still useful.

    async def fetch_already_loaded(_session: AsyncSession) -> Any:
        return filing

    return await _summarise_filing_by_url(
        user=user,
        db=db,
        feature_key=INVESTMENT_ADVISORS,
        fetch_filing=fetch_already_loaded,
        filing_label=f"{getattr(filing, 'form_type', 'Form ADV')} filing",
        firm_name=firm_name,
        detail_link=detail_link,
        question=question,
        tool_name="summarize_investment_advisor_filing",
    )


async def _execute_summarize_institutional_investor_filing(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarise an institutional investor's 13F-HR / Schedule 13D-G filing."""
    try:
        filing_id = int(args.get("filing_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": (
                "Argument 'filing_id' must be an integer. Get it from "
                "list_filings_for_firm with firm_type='ii'."
            ),
        }

    question = _opt_str(args, "question")

    async def fetcher(session: AsyncSession) -> Any:
        return (
            await session.execute(
                select(InvestorFiling).where(InvestorFiling.id == filing_id)
            )
        ).scalar_one_or_none()

    filing = await fetcher(db)
    if filing is None:
        return {
            "error": "not_found",
            "message": f"No institutional investor filing with id={filing_id}.",
        }
    investor_id = getattr(filing, "investor_id", None)
    firm_name = None
    detail_link = None
    if investor_id is not None:
        try:
            investor = await _ii_repo.get_institutional_investor(db, investor_id)
            if investor is not None:
                firm_name = getattr(investor, "name", None)
                detail_link = ii_detail_url(investor_id)
        except Exception:
            logger.exception("doxie tool failed loading investor for filing")

    async def fetch_already_loaded(_session: AsyncSession) -> Any:
        return filing

    return await _summarise_filing_by_url(
        user=user,
        db=db,
        feature_key=INSTITUTIONAL_INVESTORS,
        fetch_filing=fetch_already_loaded,
        filing_label=f"{getattr(filing, 'form_type', 'Form 13F-HR')} filing",
        firm_name=firm_name,
        detail_link=detail_link,
        question=question,
        tool_name="summarize_institutional_investor_filing",
    )


async def _execute_summarize_form4_filing(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarise the Form 4 filing behind one Form4Transaction row."""
    try:
        txn_id = int(args.get("form4_transaction_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": (
                "Argument 'form4_transaction_id' must be an integer. Get "
                "it from search_form4_filings."
            ),
        }

    question = _opt_str(args, "question")

    txn = await _form4_repo.get(db, txn_id)
    if txn is None:
        return {
            "error": "not_found",
            "message": f"No Form 4 transaction with id={txn_id}.",
        }

    async def fetch_already_loaded(_session: AsyncSession) -> Any:
        return txn

    firm_name = getattr(txn, "issuer_name", None)
    return await _summarise_filing_by_url(
        user=user,
        db=db,
        feature_key=INVESTORS,
        fetch_filing=fetch_already_loaded,
        filing_label=(
            f"Form 4 filing by "
            f"{getattr(txn, 'reporting_owner_name', 'an insider')} for "
            f"{firm_name or 'this issuer'}"
        ),
        firm_name=firm_name,
        # Form 4 has no per-transaction detail page — link to the
        # /investors page filtered by issuer ticker as the closest
        # equivalent landing spot.
        detail_link=investors_url(ticker=getattr(txn, "issuer_ticker", None)),
        question=question,
        tool_name="summarize_form4_filing",
    )


# ── Vault RAG ────────────────────────────────────────────────────────────


async def _execute_ask_vault(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """RAG against a Vault folder's chunk index.

    Reuses the existing ``vault_retrieval.retrieve_chunks`` primitive
    which already does pgvector cosine top-K over
    ``vault_folder_chunk``. The chat tool requires the caller to pass a
    ``folder_id`` and enforces per-user ownership in this layer (the
    retrieval module itself doesn't gate on user_id — it trusts the
    endpoint / tool to have already checked).

    A future iteration could let Doxie auto-pick the user's most-recent
    folder when ``folder_id`` is omitted, but for now we keep it
    explicit so the model never silently queries the wrong corpus.
    """
    denial = _check_feature(user, VAULT)
    if denial is not None:
        return denial

    query = _opt_str(args, "query")
    if not query:
        return {
            "error": "invalid_args",
            "message": "Argument 'query' is required and must be non-empty.",
        }

    try:
        folder_id = int(args.get("folder_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": (
                "Argument 'folder_id' must be an integer. The user can "
                "find their folder id under /vault."
            ),
        }

    # Enforce per-user folder ownership — vault folders are single-user
    # (no shared lists). Admins are allowed through the feature gate
    # but still don't auto-bypass folder ownership; an explicit ask
    # for someone else's folder returns not_found so the model treats
    # it as a missing folder rather than a 403.
    try:
        owner_check = await db.execute(
            select(VaultFolder.user_id).where(VaultFolder.id == folder_id)
        )
        owner_id = owner_check.scalar_one_or_none()
    except Exception:
        logger.exception("doxie tool failed", extra={"tool": "ask_vault"})
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    if owner_id is None:
        return {
            "error": "not_found",
            "message": f"No vault folder with id={folder_id}.",
        }
    if owner_id != user.id and user.role != "admin":
        # Opaque message — don't reveal that the folder exists for
        # someone else. Same shape vault_files.py uses for cross-tenant
        # protection.
        return {
            "error": "not_found",
            "message": f"No vault folder with id={folder_id}.",
        }

    try:
        raw_top_k = int(args["top_k"]) if args.get("top_k") is not None else VAULT_TOP_K_DEFAULT
    except (TypeError, ValueError):
        raw_top_k = VAULT_TOP_K_DEFAULT
    top_k = max(1, min(raw_top_k, VAULT_TOP_K_MAX))

    try:
        chunks = await retrieve_chunks(
            folder_id=folder_id, query=query, db=db, top_k=top_k
        )
    except Exception:
        logger.exception("doxie tool failed", extra={"tool": "ask_vault"})
        return {
            "error": "tool_error",
            "message": (
                "Vault retrieval failed; ask the user to try again or "
                "check that the folder finished embedding."
            ),
        }

    return {
        "items": [
            {
                "text": ch.text,
                "original_filename": ch.original_filename,
                "chunk_index": ch.chunk_index,
                "similarity": round(ch.similarity, 4),
            }
            for ch in chunks
        ],
        "total_matched": len(chunks),
        # Link to the Vault landing page — the folder UI lives under
        # /vault and shows file lists; deep-link could point at the
        # specific folder route once it lands, but bare /vault is fine.
        "link": VAULT_URL,
        "folder_id": folder_id,
    }


# ── Tool declarations ────────────────────────────────────────────────────

_SEARCH_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Firm name, CRD number, CIK, or SEC file number "
                "(substring match)."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": SEARCH_RESULT_LIMIT_MAX,
            "description": (
                f"Max rows to return. Defaults to "
                f"{SEARCH_RESULT_LIMIT_DEFAULT}, capped at "
                f"{SEARCH_RESULT_LIMIT_MAX}."
            ),
        },
    },
    "required": ["query"],
}

_BD_PROFILE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "broker_dealer_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Internal numeric id returned by search_broker_dealers."
            ),
        },
    },
    "required": ["broker_dealer_id"],
}

_IA_PROFILE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "advisor_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Internal numeric id returned by search_investment_advisors."
            ),
        },
    },
    "required": ["advisor_id"],
}


_II_PROFILE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "investor_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Internal numeric id returned by search_institutional_investors."
            ),
        },
    },
    "required": ["investor_id"],
}


_BD_LIST_FILTER_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "description": "Two-letter US state code (e.g. 'NY', 'CA').",
        },
        "status": {
            "type": "string",
            "description": "FINRA registration status. Common values: 'active', 'inactive'.",
        },
        "clearing_partner": {
            "type": "string",
            "description": (
                "Canonical clearing-partner short name (e.g. 'Pershing', "
                "'Apex', 'BNY Mellon')."
            ),
        },
        "lead_priority": {
            "type": "string",
            "description": "Lead-scoring band: 'hot', 'warm', or 'cold'.",
        },
        "min_net_capital": {
            "type": "number",
            "description": "Minimum latest_net_capital in dollars.",
        },
        "max_net_capital": {
            "type": "number",
            "description": "Maximum latest_net_capital in dollars.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": LIST_FILTER_LIMIT_MAX,
            "description": (
                f"Max rows to return. Defaults to {LIST_FILTER_LIMIT_DEFAULT}, "
                f"capped at {LIST_FILTER_LIMIT_MAX}."
            ),
        },
    },
    "required": [],
}


_SEMANTIC_SEARCH_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Free-form descriptive query — e.g. 'firms similar to "
                "Acme Securities', 'broker-dealers focused on high-net-"
                "worth retail clients'. Use only when the query is "
                "conceptual; for exact name/CRD lookups call "
                "search_broker_dealers instead."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": SEMANTIC_RESULT_LIMIT_MAX,
            "description": (
                f"Max hits to return. Defaults to "
                f"{SEMANTIC_RESULT_LIMIT_DEFAULT}, capped at "
                f"{SEMANTIC_RESULT_LIMIT_MAX}."
            ),
        },
    },
    "required": ["query"],
}


_IA_LIST_FILTER_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "description": "Two-letter US state code (e.g. 'NY', 'CA').",
        },
        "status": {
            "type": "string",
            "description": "SEC registration status. Common values: 'active', 'inactive'.",
        },
        "min_regulatory_aum": {
            "type": "number",
            "description": "Minimum regulatory_aum in dollars.",
        },
        "max_regulatory_aum": {
            "type": "number",
            "description": "Maximum regulatory_aum in dollars.",
        },
        "files_13f": {
            "type": "boolean",
            "description": "If true, restrict to advisors that file Form 13F-HR.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": LIST_FILTER_LIMIT_MAX,
            "description": (
                f"Max rows to return. Defaults to {LIST_FILTER_LIMIT_DEFAULT}, "
                f"capped at {LIST_FILTER_LIMIT_MAX}."
            ),
        },
    },
    "required": [],
}


_FORM4_SEARCH_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Free-text needle matched against reporting-owner name, "
                "issuer name, and issuer ticker (case-insensitive)."
            ),
        },
        "ad_code": {
            "type": "string",
            "enum": ["A", "D"],
            "description": (
                "'A' for insider buys (acquired), 'D' for insider sells "
                "(disposed). Omit to include both."
            ),
        },
        "ticker": {
            "type": "string",
            "description": "Issuer ticker (exact, case-insensitive). E.g. 'AAPL'.",
        },
        "days": {
            "type": "integer",
            "minimum": 1,
            "maximum": FORM4_DAYS_MAX,
            "description": (
                f"Lookback window in days. Defaults to {FORM4_DAYS_DEFAULT}, "
                f"capped at {FORM4_DAYS_MAX}."
            ),
        },
        "min_value": {
            "type": "number",
            "description": "Minimum aggregated transaction_value in dollars.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": FORM4_RESULT_LIMIT_MAX,
            "description": (
                f"Max rows to return. Defaults to {FORM4_RESULT_LIMIT_DEFAULT}, "
                f"capped at {FORM4_RESULT_LIMIT_MAX}."
            ),
        },
    },
    "required": [],
}


_FILINGS_FOR_FIRM_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "firm_type": {
            "type": "string",
            "enum": ["bd", "ia", "ii"],
            "description": (
                "Which firm table to read: 'bd' (broker-dealers — Form X-17A-5 "
                "/ FOCUS), 'ia' (investment advisors — Form ADV / 13F-HR), "
                "or 'ii' (institutional investors — Form 13F-HR / Schedule "
                "13D-G)."
            ),
        },
        "firm_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Internal numeric id of the firm. Get this from the search / "
                "profile tools (broker_dealer_id, advisor_id, investor_id)."
            ),
        },
        "form_type": {
            "type": "string",
            "description": (
                "Optional exact form-type filter (e.g. 'Form X-17A-5', "
                "'Form ADV', 'Form 13F-HR'). Case-sensitive match."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": FILINGS_LIST_LIMIT_MAX,
            "description": (
                f"Max filings to return. Defaults to "
                f"{FILINGS_LIST_LIMIT_DEFAULT}, capped at "
                f"{FILINGS_LIST_LIMIT_MAX}."
            ),
        },
    },
    "required": ["firm_type", "firm_id"],
}


_RECENT_ALERTS_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "form_type": {
            "type": "string",
            "description": (
                "Exact form-type filter, e.g. 'Form BD', 'Form X-17A-5', "
                "'Form 17a-11'."
            ),
        },
        "priority": {
            "type": "string",
            "description": "Priority band: 'high', 'medium', or 'low'.",
        },
        "broker_dealer_id": {
            "type": "integer",
            "minimum": 1,
            "description": "Restrict to alerts for one broker-dealer.",
        },
        "is_read": {
            "type": "boolean",
            "description": "If false, only return unread alerts. Omit for all.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": ALERTS_RESULT_LIMIT_MAX,
            "description": (
                f"Max alerts to return. Defaults to "
                f"{ALERTS_RESULT_LIMIT_DEFAULT}, capped at "
                f"{ALERTS_RESULT_LIMIT_MAX}."
            ),
        },
    },
    "required": [],
}


# Shared question knob across all PDF-summarise tools.
_FILING_QUESTION_FIELD: dict[str, Any] = {
    "type": "string",
    "description": (
        "Optional natural-language question to focus the summary on. "
        "E.g. 'What's the net capital trend?' or 'What clearing "
        "partners are mentioned?'. The model will still ground "
        "the answer entirely in the document."
    ),
}


_SUMMARIZE_BD_FILING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "broker_dealer_id": {
            "type": "integer",
            "minimum": 1,
            "description": "Internal numeric id of the broker-dealer.",
        },
        "question": _FILING_QUESTION_FIELD,
    },
    "required": ["broker_dealer_id"],
}


_SUMMARIZE_BROKERCHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "broker_dealer_id": {
            "type": "integer",
            "minimum": 1,
            "description": "Internal numeric id of the broker-dealer.",
        },
        "question": _FILING_QUESTION_FIELD,
    },
    "required": ["broker_dealer_id"],
}


_SUMMARIZE_FILING_BY_ID_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filing_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Numeric filing id returned by list_filings_for_firm. "
                "Identifies one row in advisor_filings / investor_filings."
            ),
        },
        "question": _FILING_QUESTION_FIELD,
    },
    "required": ["filing_id"],
}


_SUMMARIZE_FORM4_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "form4_transaction_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Numeric transaction id from search_form4_filings (the "
                "'id' field is unprojected — call list_filings_for_firm "
                "with firm_type='ii' is NOT correct here; this is a Form "
                "4 transaction row, not a 13F filing)."
            ),
        },
        "question": _FILING_QUESTION_FIELD,
    },
    "required": ["form4_transaction_id"],
}


_ASK_VAULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Free-form natural-language question. Embedded with the "
                "same gemini-embedding-001 model used to index the "
                "vault, then cosine top-K against the folder's chunk "
                "index."
            ),
        },
        "folder_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Numeric id of the Vault folder to search. The user can "
                "find it under /vault. Restricted to folders owned by "
                "the calling user."
            ),
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": VAULT_TOP_K_MAX,
            "description": (
                f"Max chunks to return. Defaults to {VAULT_TOP_K_DEFAULT}, "
                f"capped at {VAULT_TOP_K_MAX}."
            ),
        },
    },
    "required": ["query", "folder_id"],
}


TOOL_REGISTRY: dict[str, Tool] = {
    "search_broker_dealers": Tool(
        name="search_broker_dealers",
        description=(
            "Search SEC-registered broker-dealer firms by name, CRD number, "
            "CIK, or SEC file number (substring match). Use when the user "
            "mentions a broker-dealer by name or identifier and you need a "
            "list of candidates."
        ),
        parameters_schema=_SEARCH_PARAMETERS_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_search_broker_dealers,
    ),
    "get_broker_dealer_profile": Tool(
        name="get_broker_dealer_profile",
        description=(
            "Fetch one broker-dealer's full profile by internal numeric ID. "
            "Returns registration, net capital, year-over-year growth, "
            "clearing arrangements, and the most recent financial periods. "
            "Call after search_broker_dealers once a specific firm is "
            "identified."
        ),
        parameters_schema=_BD_PROFILE_PARAMETERS_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_get_broker_dealer_profile,
    ),
    "search_investment_advisors": Tool(
        name="search_investment_advisors",
        description=(
            "Search SEC-registered investment advisors (Form ADV filers) by "
            "name, CRD, or CIK. Returns AUM and basic profile fields."
        ),
        parameters_schema=_SEARCH_PARAMETERS_SCHEMA,
        feature_key=INVESTMENT_ADVISORS,
        execute=_execute_search_investment_advisors,
    ),
    "get_investment_advisor_profile": Tool(
        name="get_investment_advisor_profile",
        description=(
            "Fetch one investment advisor's full profile by numeric ID. "
            "Returns AUM split (regulatory / discretionary / "
            "non-discretionary), advisory activities, client mix, and "
            "registration / filing dates."
        ),
        parameters_schema=_IA_PROFILE_PARAMETERS_SCHEMA,
        feature_key=INVESTMENT_ADVISORS,
        execute=_execute_get_investment_advisor_profile,
    ),
    "search_institutional_investors": Tool(
        name="search_institutional_investors",
        description=(
            "Search SEC Form 13F filers (institutional investors / asset "
            "managers) by name, legal name, or CIK. Returns total_aum, "
            "holdings_count, and basic profile fields. Use for any "
            "'who holds X' or 'who's the asset manager called Y' question."
        ),
        parameters_schema=_SEARCH_PARAMETERS_SCHEMA,
        feature_key=INSTITUTIONAL_INVESTORS,
        execute=_execute_search_institutional_investors,
    ),
    "get_institutional_investor_profile": Tool(
        name="get_institutional_investor_profile",
        description=(
            "Fetch one institutional investor's full profile by numeric ID. "
            "Returns total AUM, holdings count, latest 13F filing date, and "
            "the linked investment-advisor id when the investor is also "
            "registered as an IA."
        ),
        parameters_schema=_II_PROFILE_PARAMETERS_SCHEMA,
        feature_key=INSTITUTIONAL_INVESTORS,
        execute=_execute_get_institutional_investor_profile,
    ),
    "list_broker_dealers_by_filter": Tool(
        name="list_broker_dealers_by_filter",
        description=(
            "List broker-dealers matching criteria (state / status / "
            "clearing partner / lead priority / net capital band). At least "
            "one filter is required. Use for 'who clears through Pershing in "
            "California' or 'show me hot leads with net capital above $5M' "
            "style queries. Use search_broker_dealers (not this tool) when "
            "the user names a specific firm."
        ),
        parameters_schema=_BD_LIST_FILTER_PARAMETERS_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_list_broker_dealers_by_filter,
    ),
    "list_investment_advisors_by_filter": Tool(
        name="list_investment_advisors_by_filter",
        description=(
            "List investment advisors matching criteria (state / status / "
            "AUM band / files_13f flag). At least one filter is required. "
            "Use search_investment_advisors instead when the user names a "
            "specific firm."
        ),
        parameters_schema=_IA_LIST_FILTER_PARAMETERS_SCHEMA,
        feature_key=INVESTMENT_ADVISORS,
        execute=_execute_list_investment_advisors_by_filter,
    ),
    "semantic_firm_search": Tool(
        name="semantic_firm_search",
        description=(
            "Conceptual / semantic search over broker-dealer firms via "
            "vector embeddings. Use when the user's query is descriptive "
            "rather than naming a specific firm — examples: 'firms similar "
            "to Acme Securities', 'broker-dealers focused on retail HNW', "
            "'small BDs that clear self'. For exact name / CRD lookups, "
            "call search_broker_dealers instead. Returns hits with a "
            "similarity score and a snippet of the matched summary."
        ),
        parameters_schema=_SEMANTIC_SEARCH_PARAMETERS_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_semantic_firm_search,
    ),
    "search_form4_filings": Tool(
        name="search_form4_filings",
        description=(
            "Search recent Form 4 insider transactions. Form 4 is filed "
            "by corporate insiders (directors, officers, 10%+ owners) within "
            "~2 business days of buying or selling their company's stock. "
            "At least one of query / ad_code / ticker / min_value must be "
            "set (no whole-table dumps). Use ad_code='A' for insider buys, "
            "'D' for sells. Results are aggregated per (insider × issuer × "
            "buy/sell) so one row summarises a person's total recent "
            "activity in one stock."
        ),
        parameters_schema=_FORM4_SEARCH_PARAMETERS_SCHEMA,
        feature_key=INVESTORS,
        execute=_execute_search_form4_filings,
    ),
    "list_filings_for_firm": Tool(
        name="list_filings_for_firm",
        description=(
            "Enumerate recent filings attached to a specific broker-dealer, "
            "investment advisor, or institutional investor. Returns metadata "
            "(filing_id, form_type, filed_at, summary, source_filing_url) — "
            "NOT the PDF contents. The filing_id this returns is the handle "
            "the future PDF-summarize tools will accept to fetch the "
            "document. Call after a search/profile tool has identified the "
            "firm id."
        ),
        parameters_schema=_FILINGS_FOR_FIRM_PARAMETERS_SCHEMA,
        # ``feature_key`` is set per-firm-type inside the execute func;
        # this top-level value is informational only.
        feature_key=MASTER_LIST,
        execute=_execute_list_filings_for_firm,
    ),
    "get_recent_alerts": Tool(
        name="get_recent_alerts",
        description=(
            "Recent filing-alerts feed (the data behind the /alerts page). "
            "Useful for 'what's new this week?' or 'any new Form BD "
            "registrations?' style questions. Optional filters: form_type, "
            "priority, broker_dealer_id, is_read."
        ),
        parameters_schema=_RECENT_ALERTS_PARAMETERS_SCHEMA,
        feature_key=ALERTS,
        execute=_execute_get_recent_alerts,
    ),
    "summarize_broker_dealer_filing": Tool(
        name="summarize_broker_dealer_filing",
        description=(
            "Read and summarize a broker-dealer's most recent Form X-17A-5 "
            "(FOCUS) annual filing from SEC EDGAR. Returns a prose summary "
            "of the document plus the source PDF URL. Use when the user "
            "wants narrative context about a BD's audited financials, net "
            "capital trend, clearing arrangements, or deficiencies. "
            "Optional 'question' biases the summary toward what the user "
            "actually asked. Slow (~5-15s); the chat will surface a "
            "'looking up' indicator."
        ),
        parameters_schema=_SUMMARIZE_BD_FILING_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_summarize_broker_dealer_filing,
        timeout_s=PDF_TOOL_TIMEOUT_S,
        cacheable=False,
    ),
    "summarize_brokercheck_pdf": Tool(
        name="summarize_brokercheck_pdf",
        description=(
            "Read and summarize the FINRA BrokerCheck Detailed Report PDF "
            "for a broker-dealer. Different doc family from Form X-17A-5: "
            "covers disclosures, branch counts, employment history, "
            "customer complaints, and regulatory actions — not the audited "
            "financials. Use for 'what's their disclosure history?' / "
            "'any regulatory actions?' style prompts."
        ),
        parameters_schema=_SUMMARIZE_BROKERCHECK_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_summarize_brokercheck_pdf,
        timeout_s=PDF_TOOL_TIMEOUT_S,
        cacheable=False,
    ),
    "summarize_investment_advisor_filing": Tool(
        name="summarize_investment_advisor_filing",
        description=(
            "Read and summarize one Form ADV / ADV-W / 13F-HR filing "
            "attached to an investment advisor. Call list_filings_for_firm "
            "first with firm_type='ia' to get a filing_id, then pass it "
            "here. The PDF is fetched from SEC EDGAR; some advisor "
            "filings link to an HTML index page rather than a direct PDF "
            "— in that case the tool returns a clear error with the "
            "source URL so you can offer the link."
        ),
        parameters_schema=_SUMMARIZE_FILING_BY_ID_SCHEMA,
        feature_key=INVESTMENT_ADVISORS,
        execute=_execute_summarize_investment_advisor_filing,
        timeout_s=PDF_TOOL_TIMEOUT_S,
        cacheable=False,
    ),
    "summarize_institutional_investor_filing": Tool(
        name="summarize_institutional_investor_filing",
        description=(
            "Read and summarize one 13F-HR / Schedule 13D-G filing "
            "attached to an institutional investor. Same flow as the "
            "advisor variant: list_filings_for_firm with firm_type='ii' → "
            "filing_id → this tool."
        ),
        parameters_schema=_SUMMARIZE_FILING_BY_ID_SCHEMA,
        feature_key=INSTITUTIONAL_INVESTORS,
        execute=_execute_summarize_institutional_investor_filing,
        timeout_s=PDF_TOOL_TIMEOUT_S,
        cacheable=False,
    ),
    "summarize_form4_filing": Tool(
        name="summarize_form4_filing",
        description=(
            "Read and summarize the Form 4 filing behind one insider "
            "transaction row. Use after search_form4_filings has surfaced "
            "an interesting transaction id. Most Form 4 EDGAR URLs point "
            "at the filing index rather than a single PDF — the tool may "
            "surface that as an error with the source URL."
        ),
        parameters_schema=_SUMMARIZE_FORM4_SCHEMA,
        feature_key=INVESTORS,
        execute=_execute_summarize_form4_filing,
        timeout_s=PDF_TOOL_TIMEOUT_S,
        cacheable=False,
    ),
    "ask_vault": Tool(
        name="ask_vault",
        description=(
            "RAG over a user's Vault folder. Embeds the question with "
            "gemini-embedding-001, runs pgvector cosine top-K against "
            "the folder's pre-indexed chunks, and returns the matched "
            "passages with their source filename. Restricted to folders "
            "owned by the calling user. Use when the user references "
            "internal docs they've uploaded (e.g. compliance playbooks, "
            "internal memos)."
        ),
        parameters_schema=_ASK_VAULT_SCHEMA,
        feature_key=VAULT,
        execute=_execute_ask_vault,
        # Vault retrieval is cheap (one embedding + one pg query); the
        # default 5s is fine. Caching is also OK because retrieve_chunks
        # is purely a function of (folder_id, query) at a point in time
        # and the chat-level TTL is short.
    ),
}
