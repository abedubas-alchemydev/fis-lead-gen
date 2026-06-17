"""Doxie aggregate-analytics tools — firm counts/rankings + clearing-partner lookup.

Implementations live here (not in ``chatbot_tools``) so that registry module
only needs a two-line registration hunk at its end; ``chatbot_tools`` calls
``build_analytics_tools()`` after ``TOOL_REGISTRY`` is defined and merges the
result in. Because the two modules reference each other, every import FROM
``chatbot_tools`` here is deferred to call time — this module has no
module-scope dependency on it, so either import order resolves cleanly.

Semantics deliberately mirror the master-list / advisor-list surfaces so
Doxie's numbers match what the user sees in the UI:

- Filters use the exact predicates ``BrokerDealerRepository
  .list_broker_dealers`` / ``InvestmentAdvisorRepository
  .list_investment_advisors`` apply (state/status/clearing_type exact
  ``IN`` match; tri-state ``files_13f``), over the same "all firms"
  universe ``list_broker_dealers_by_filter`` queries (no primary-list
  deficiency filter). Deep-links carry ``list=all`` so the master-list
  total the user lands on equals the total reported here.
- Clearing partner reads the denormalized
  ``broker_dealers.current_clearing_partner`` / ``current_clearing_type``
  rollup columns — the canonical source the master-list filter, dropdown,
  and dashboard distribution query. (``clearing_arrangements`` rows roll
  up into them via ``apply_clearing_rollup``; the rollup picks the
  authoritative arrangement, so re-deriving from the child table here
  would double-count multi-year filers and drift from the UI.) Raw partner
  variants consolidate to the same canonical labels the dropdown renders,
  via ``auto_group_long_tail`` with the live provider registry + admin-
  rejected cluster signatures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feature_permissions import INVESTMENT_ADVISORS, MASTER_LIST
from app.models.broker_dealer import BrokerDealer
from app.models.investment_advisor import InvestmentAdvisor
from app.schemas.auth import AuthenticatedUser
from app.services.chatbot_urls import bd_detail_url, bd_list_url, ia_list_url
from app.services.clearing_auto_group import (
    auto_group_long_tail,
    load_rejected_signatures,
)
from app.services.clearing_consolidation import (
    load_providers as load_consolidation_providers,
)

if TYPE_CHECKING:
    from app.services.chatbot_tools import Tool

logger = logging.getLogger(__name__)

AGGREGATE_TOP_N_DEFAULT = 10
AGGREGATE_TOP_N_MAX = 25
CLEARING_FIRMS_LIMIT_DEFAULT = 15
CLEARING_FIRMS_LIMIT_MAX = 50

# NULL / blank group values are reported under this bucket rather than
# silently dropped — "12 firms have no clearing type on file" is signal.
UNKNOWN_GROUP_LABEL = "unknown"

# group_by name -> ORM column, per entity type. clearing_type / clearing_
# partner use the current_* rollup columns (see module docstring).
_BD_GROUP_COLUMNS = {
    "state": BrokerDealer.state,
    "status": BrokerDealer.status,
    "clearing_type": BrokerDealer.current_clearing_type,
    "clearing_partner": BrokerDealer.current_clearing_partner,
    "lead_priority": BrokerDealer.lead_priority,
    "health_status": BrokerDealer.health_status,
}
_IA_GROUP_COLUMNS = {
    "state": InvestmentAdvisor.state,
    "status": InvestmentAdvisor.status,
}

# metric name -> column it aggregates. The columns match the list pages'
# net-capital / AUM filters and default sorts.
_BD_METRIC_COLUMNS = {
    "avg_net_capital": BrokerDealer.latest_net_capital,
    "total_net_capital": BrokerDealer.latest_net_capital,
}
_IA_METRIC_COLUMNS = {
    "avg_aum": InvestmentAdvisor.regulatory_aum,
    "total_aum": InvestmentAdvisor.regulatory_aum,
}


def _clamp_int(raw: Any, default: int, maximum: int) -> int:
    try:
        n = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, maximum))


def _invalid(message: str) -> dict[str, Any]:
    return {"error": "invalid_args", "message": message}


@dataclass
class _Bucket:
    """One merged group: row count plus the metric column's sum / non-NULL n."""

    count: int = 0
    metric_sum: float = 0.0
    metric_n: int = 0


async def _clearing_partner_label_map(
    db: AsyncSession, raw_values: list[str]
) -> dict[str, str]:
    """Map each raw ``current_clearing_partner`` value to its canonical label.

    Same ``auto_group_long_tail`` pass (provider registry + rejected cluster
    signatures) the master-list dropdown and filter expansion run, so the
    buckets here can't drift from what the user sees in the UI.
    """
    providers = await load_consolidation_providers(db)
    rejected = await load_rejected_signatures(db)
    groups = auto_group_long_tail(list(raw_values), providers, rejected)
    return {raw: label for label, members in groups.items() for raw in members}


# ── get_firm_aggregates ──────────────────────────────────────────────────


async def _execute_get_firm_aggregates(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Count/rank firms grouped by one field, with optional list-style filters.

    Answers "how many BDs per state?", "top clearing partners by firm
    count", "average AUM of advisors by state". Aggregate-only — the
    list/search tools return actual firm rows.
    """
    from app.services.chatbot_tools import _check_feature, _jsonable, _opt_bool, _opt_str

    entity_type = (_opt_str(args, "entity_type") or "").lower()
    if entity_type not in ("broker_dealer", "investment_advisor"):
        return _invalid(
            "Argument 'entity_type' must be 'broker_dealer' or 'investment_advisor'."
        )
    is_bd = entity_type == "broker_dealer"

    denial = _check_feature(user, MASTER_LIST if is_bd else INVESTMENT_ADVISORS)
    if denial is not None:
        return denial

    group_columns = _BD_GROUP_COLUMNS if is_bd else _IA_GROUP_COLUMNS
    group_by = (_opt_str(args, "group_by") or "").lower()
    if group_by not in group_columns:
        return _invalid(
            f"Argument 'group_by' for {entity_type} must be one of: "
            f"{', '.join(group_columns)}."
        )

    metric_columns = _BD_METRIC_COLUMNS if is_bd else _IA_METRIC_COLUMNS
    metric = (_opt_str(args, "metric") or "count").lower()
    if metric != "count" and metric not in metric_columns:
        return _invalid(
            f"Argument 'metric' for {entity_type} must be one of: "
            f"count, {', '.join(metric_columns)}."
        )

    state = _opt_str(args, "state")
    status = _opt_str(args, "status")
    clearing_type = _opt_str(args, "clearing_type")
    files_13f = _opt_bool(args, "files_13f")
    if not is_bd and clearing_type is not None:
        return _invalid(
            "Filter 'clearing_type' only applies to entity_type='broker_dealer'."
        )
    if is_bd and files_13f is not None:
        return _invalid(
            "Filter 'files_13f' only applies to entity_type='investment_advisor'."
        )

    # Same predicates the list endpoints build from these filters.
    filters = []
    if is_bd:
        if state:
            filters.append(BrokerDealer.state.in_([state]))
        if status:
            filters.append(BrokerDealer.status.in_([status]))
        if clearing_type:
            filters.append(BrokerDealer.current_clearing_type.in_([clearing_type]))
    else:
        if state:
            filters.append(InvestmentAdvisor.state.in_([state]))
        if status:
            filters.append(InvestmentAdvisor.status.in_([status]))
        if files_13f is True:
            filters.append(InvestmentAdvisor.files_13f.is_(True))
        elif files_13f is False:
            filters.append(InvestmentAdvisor.files_13f.is_(False))

    top_n = _clamp_int(args.get("top_n"), AGGREGATE_TOP_N_DEFAULT, AGGREGATE_TOP_N_MAX)
    model = BrokerDealer if is_bd else InvestmentAdvisor
    group_col = group_columns[group_by]

    stmt = select(group_col.label("group_value"), func.count(model.id).label("row_count"))
    if metric != "count":
        metric_col = metric_columns[metric]
        # sum + non-NULL count per group so consolidated buckets can merge
        # into an exact weighted average (avg-of-avgs would be wrong).
        stmt = stmt.add_columns(
            func.sum(metric_col).label("metric_sum"),
            func.count(metric_col).label("metric_n"),
        )
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.group_by(group_col)

    try:
        rows = (await db.execute(stmt)).all()

        raw_to_label: dict[str, str] = {}
        if group_by == "clearing_partner":
            raws = [r.group_value for r in rows if r.group_value and str(r.group_value).strip()]
            raw_to_label = await _clearing_partner_label_map(db, raws)
    except Exception:
        logger.exception("doxie tool failed", extra={"tool": "get_firm_aggregates"})
        return {
            "error": "tool_error",
            "message": "Aggregation failed; ask the user to try again.",
        }

    # Merge SQL groups into display buckets. Plain group-bys map 1:1; the
    # clearing-partner path folds raw variants under one canonical label.
    buckets: dict[str, _Bucket] = {}
    for row in rows:
        value = row.group_value
        text = str(value).strip() if value is not None else ""
        label = raw_to_label.get(value, text) if text else UNKNOWN_GROUP_LABEL
        bucket = buckets.setdefault(label, _Bucket())
        bucket.count += int(row.row_count)
        if metric != "count":
            if row.metric_sum is not None:
                bucket.metric_sum += float(row.metric_sum)
            bucket.metric_n += int(row.metric_n)

    def _metric_value(bucket: _Bucket) -> float | None:
        if bucket.metric_n == 0:
            return None
        if metric.startswith("avg_"):
            return round(bucket.metric_sum / bucket.metric_n, 2)
        return round(bucket.metric_sum, 2)

    if metric == "count":
        ordered = sorted(buckets.items(), key=lambda kv: (-kv[1].count, kv[0].lower()))
    else:
        # Metric desc with no-data buckets last; count breaks ties.
        ordered = sorted(
            buckets.items(),
            key=lambda kv: (
                _metric_value(kv[1]) is None,
                -(_metric_value(kv[1]) or 0.0),
                -kv[1].count,
                kv[0].lower(),
            ),
        )

    groups: list[dict[str, Any]] = []
    for label, bucket in ordered[:top_n]:
        entry: dict[str, Any] = {"group_value": label, "count": bucket.count}
        if metric != "count":
            entry["metric_value"] = _metric_value(bucket)
        groups.append(entry)

    if is_bd:
        # list=all so the linked page shows the exact universe counted here
        # (the default primary list hides deficient firms). status has no
        # master-list URL param, so it can't be echoed.
        list_link = bd_list_url(state=state, clearing_type=clearing_type, list_mode="all")
    else:
        # No files_13f filter means we counted ALL advisors, so send
        # ``files_13f=all`` — otherwise the FE re-applies its hard 13F-only
        # default and the landing total undercounts what we reported (same
        # pattern as search_investment_advisors).
        list_link = ia_list_url(
            state=state,
            status=status if status != "All" else None,
            files_13f="all" if files_13f is None else files_13f,
        )

    return _jsonable(
        {
            "entity_type": entity_type,
            "group_by": group_by,
            "metric": metric,
            "total_firms": sum(b.count for b in buckets.values()),
            "groups": groups,
            "groups_total": len(buckets),
            "list_link": list_link,
        }
    )


# ── list_firms_by_clearing_partner ───────────────────────────────────────


async def _execute_list_firms_by_clearing_partner(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Broker-dealers clearing through a partner (substring on canonical labels).

    Matches the needle against the consolidated dropdown labels AND their
    underlying raw variants, then filters by the matched groups' raw values
    — exactly how a dropdown selection expands on the master list.
    """
    from app.services.chatbot_tools import _check_feature, _jsonable, _opt_str

    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial

    partner_name = _opt_str(args, "partner_name")
    if not partner_name:
        return _invalid("Argument 'partner_name' is required and must be non-empty.")
    limit = _clamp_int(
        args.get("limit"), CLEARING_FIRMS_LIMIT_DEFAULT, CLEARING_FIRMS_LIMIT_MAX
    )

    try:
        distinct_stmt = (
            select(BrokerDealer.current_clearing_partner)
            .where(BrokerDealer.current_clearing_partner.is_not(None))
            .distinct()
        )
        distinct_raw = list((await db.execute(distinct_stmt)).scalars().all())
        providers = await load_consolidation_providers(db)
        rejected = await load_rejected_signatures(db)
        groups = auto_group_long_tail(distinct_raw, providers, rejected)

        needle = partner_name.lower()
        matched_labels = sorted(
            (
                label
                for label, members in groups.items()
                if needle in label.lower()
                or any(needle in member.lower() for member in members)
            ),
            key=str.lower,
        )
        if not matched_labels:
            return {
                "matched_partners": [],
                "items": [],
                "total_matched": 0,
                "note": (
                    f"No clearing partner on file matches '{partner_name}'. "
                    "Partner names come from each firm's latest X-17A-5 extraction."
                ),
            }

        matching_raw = {raw for label in matched_labels for raw in groups[label]}
        firm_filter = BrokerDealer.current_clearing_partner.in_(matching_raw)
        total = int(
            (
                await db.execute(select(func.count(BrokerDealer.id)).where(firm_filter))
            ).scalar_one()
        )
        firm_stmt = (
            select(BrokerDealer)
            .where(firm_filter)
            .order_by(
                BrokerDealer.latest_net_capital.desc().nullslast(),
                BrokerDealer.id.asc(),
            )
            .limit(limit)
        )
        firms = (await db.execute(firm_stmt)).scalars().all()
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "list_firms_by_clearing_partner"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }

    return _jsonable(
        {
            "matched_partners": matched_labels,
            "items": [
                {
                    "id": firm.id,
                    "name": firm.name,
                    "crd_number": firm.crd_number,
                    "state": firm.state,
                    "clearing_type": firm.current_clearing_type,
                    "clearing_partner": firm.current_clearing_partner,
                    "link": bd_detail_url(firm.id),
                }
                for firm in firms
            ],
            "total_matched": total,
            # The master list supports repeat-key ?clearing_partner=<label>
            # deep-links; list=all keeps the landing total equal to ours.
            "list_link": bd_list_url(
                clearing_partner=matched_labels, list_mode="all"
            ),
        }
    )


# ── Tool declarations ────────────────────────────────────────────────────

_FIRM_AGGREGATES_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entity_type": {
            "type": "string",
            "enum": ["broker_dealer", "investment_advisor"],
            "description": "Which firm registry to aggregate over.",
        },
        "group_by": {
            "type": "string",
            "enum": [
                "state",
                "status",
                "clearing_type",
                "clearing_partner",
                "lead_priority",
                "health_status",
            ],
            "description": (
                "Field to group firms by. broker_dealer supports every "
                "value; investment_advisor supports only 'state' / 'status'."
            ),
        },
        "metric": {
            "type": "string",
            "enum": [
                "count",
                "avg_net_capital",
                "total_net_capital",
                "avg_aum",
                "total_aum",
            ],
            "description": (
                "Aggregate per group. Default 'count'; *_net_capital are "
                "broker_dealer-only, *_aum are investment_advisor-only."
            ),
        },
        "state": {
            "type": "string",
            "description": "Filter: two-letter US state code (e.g. 'NY', 'CA').",
        },
        "status": {
            "type": "string",
            "description": (
                "Filter: registration status. Common values: 'active', 'inactive'."
            ),
        },
        "clearing_type": {
            "type": "string",
            "description": (
                "Filter (broker_dealer only): 'fully_disclosed', "
                "'self_clearing', 'omnibus', 'non_carrying', or 'unknown'."
            ),
        },
        "files_13f": {
            "type": "boolean",
            "description": (
                "Filter (investment_advisor only): true restricts to Form "
                "13F-HR filers."
            ),
        },
        "top_n": {
            "type": "integer",
            "minimum": 1,
            "maximum": AGGREGATE_TOP_N_MAX,
            "description": (
                f"Max groups to return. Defaults to {AGGREGATE_TOP_N_DEFAULT}, "
                f"capped at {AGGREGATE_TOP_N_MAX}."
            ),
        },
    },
    "required": ["entity_type", "group_by"],
}

_FIRMS_BY_CLEARING_PARTNER_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "partner_name": {
            "type": "string",
            "description": (
                "Clearing-partner name or fragment, case-insensitive — "
                "e.g. 'Pershing', 'apex', 'RBC'."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": CLEARING_FIRMS_LIMIT_MAX,
            "description": (
                f"Max firms to return. Defaults to "
                f"{CLEARING_FIRMS_LIMIT_DEFAULT}, capped at "
                f"{CLEARING_FIRMS_LIMIT_MAX}."
            ),
        },
    },
    "required": ["partner_name"],
}


def build_analytics_tools() -> dict[str, "Tool"]:
    """Tool entries for ``chatbot_tools.TOOL_REGISTRY`` (called at its end)."""
    from app.services.chatbot_tools import Tool

    return {
        "get_firm_aggregates": Tool(
            name="get_firm_aggregates",
            description=(
                "Count or rank broker-dealers / investment advisors grouped "
                "by a field — 'how many BDs per state?', 'top clearing "
                "partners by firm count', 'average net capital by clearing "
                "type'. Returns the total matching firms plus ordered "
                "groups (NULL group values bucketed as 'unknown') and a "
                "pre-filtered list link. Aggregate numbers only — use the "
                "list/search tools when the user wants actual firm rows."
            ),
            parameters_schema=_FIRM_AGGREGATES_PARAMETERS_SCHEMA,
            # Nominal key — execute gates on MASTER_LIST or
            # INVESTMENT_ADVISORS per entity_type, like list_filings_for_firm.
            feature_key=MASTER_LIST,
            execute=_execute_get_firm_aggregates,
        ),
        "list_firms_by_clearing_partner": Tool(
            name="list_firms_by_clearing_partner",
            description=(
                "List the broker-dealers that clear through a given clearing "
                "partner (case-insensitive name match — 'who clears through "
                "Pershing?'). Returns the matched canonical partner label(s), "
                "firm rows, total count, and a master-list link pre-filtered "
                "to those partners. To find which partner one specific firm "
                "uses, call get_broker_dealer_profile instead."
            ),
            parameters_schema=_FIRMS_BY_CLEARING_PARTNER_PARAMETERS_SCHEMA,
            feature_key=MASTER_LIST,
            execute=_execute_list_firms_by_clearing_partner,
        ),
    }
