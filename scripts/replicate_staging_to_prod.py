#!/usr/bin/env python3
"""Keyed-merge replication of STAGING enrichment/reference data into PRODUCTION.

Goal
----
Bring staging's enrichment / reference tables (broker-dealers, advisors,
contacts, filings, clearing data, embeddings, ...) into prod while PRESERVING
prod's live user data. The merge is *keyed*: rows are matched by a stable
business/natural key, existing prod rows are UPDATEd in place (their surrogate
``id`` is never renumbered), genuinely-new staging rows are INSERTed (prod
assigns fresh ids), and child foreign keys are remapped from staging-parent-id
to prod-parent-id via a per-parent ``staging_id -> prod_id`` map.

Why id-preservation matters
---------------------------
Prod USER tables reference DATA-table surrogate ids directly:
``favorite_list_item.broker_dealer_id -> broker_dealers.id``,
``user_visit.bd_id``, ``outreach_sends.*``, plus the *soft* reference
``chatbot_firm_embedding.entity_id``. If the merge renumbered a firm's id,
every saved favorite / visit would silently repoint to the wrong firm. This
script therefore NEVER deletes a prod row and NEVER changes a prod row's id --
it only updates columns in place or inserts brand-new rows.

Safety model (invariants this script guarantees)
-----------------------------------------------
1. USER / SESSION / AUTH tables are EXCLUDED and never read-for-write or
   written. See ``EXCLUDE_TABLES``.
2. No prod row is ever DELETEd. No prod ``id`` is ever changed.
3. A matched (UPDATE) row is never NULLed-out: every writable column uses
   ``COALESCE(staging, prod)`` by default (staging fills/overwrites with a
   value; a staging NULL keeps prod's value). ``--fill-only`` flips this to
   ``COALESCE(prod, staging)`` (prod always wins; only NULL gaps filled).
4. DRY-RUN (default) opens BOTH connections read-only and issues ZERO writes.
5. ``--apply`` performs ALL writes inside ONE transaction (all-or-nothing).

Usage
-----
    # Dry-run (default) -- reports the plan, writes nothing:
    python scripts/replicate_staging_to_prod.py \
        --source-url "$DATABASE_URL_BACKEND_STAGING" \
        --target-url "$DATABASE_URL_BACKEND"

    # Apply (single transaction) -- only after human review of the dry-run:
    python scripts/replicate_staging_to_prod.py --apply \
        --source-url ... --target-url ... --i-understand-this-writes-to-prod

URLs default to env ``DATABASE_URL_BACKEND_STAGING`` / ``DATABASE_URL_BACKEND``
if the flags are omitted. The SQLAlchemy driver suffix (``+psycopg`` /
``+asyncpg``) is stripped automatically. URLs are NEVER printed; connection
errors are scrubbed of credentials.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Json, Jsonb

# ─────────────────────────────────────────────────────────────────────────────
# DSN handling (never printed; errors scrubbed)
# ─────────────────────────────────────────────────────────────────────────────


def normalize_dsn(dsn: str) -> str:
    return (
        dsn.replace("postgresql+psycopg://", "postgresql://")
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres+psycopg://", "postgresql://")
    )


def scrub(msg: str, *dsns: str) -> str:
    out = msg
    for dsn in dsns:
        if not dsn:
            continue
        out = out.replace(dsn, "<dsn-redacted>")
        if "@" in dsn and "//" in dsn:
            try:
                creds = dsn.split("//", 1)[1].split("@", 1)[0]
                out = out.replace(creds, "<credentials-redacted>")
                if ":" in creds:
                    out = out.replace(creds.split(":", 1)[1], "<pw-redacted>")
            except Exception:
                pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────

# (A) EXCLUDE — prod's live user/session/auth data + operational logs + config.
# These are NEVER read-for-write and NEVER written.
EXCLUDE_TABLES: dict[str, str] = {
    # --- user / session / auth (prod's real user data) ---
    "user": "auth: BetterAuth user accounts (prod's live users)",
    "account": "auth: BetterAuth provider accounts (FK user)",
    "session": "auth: BetterAuth sessions (FK user)",
    "verification": "auth: BetterAuth email-verification tokens (identifier=email)",
    "audit_log": "user: admin/audit trail (FK user)",
    "user_activity": "user: per-user activity log (FK user)",
    "user_visit": "user: per-user firm visits (FK user + bd)",
    "favorite_list": "user: user-owned lists (FK user)",
    "favorite_list_item": "user: list items referencing DATA firm ids (FK user-list)",
    "chatbot_conversation": "user: per-user chatbot threads (FK user)",
    "chatbot_message": "user: chatbot messages (FK conversation->user)",
    "chatbot_user_memory": "user: per-user chatbot memory (FK user)",
    "outreach_drafts": "user: per-user outreach drafts (FK user)",
    "outreach_sends": "user: per-user outreach sends (FK user)",
    "user_outreach_settings": "user: per-user settings (PK=user_id)",
    "vault_folder": "user: per-user document vault (FK user)",
    "vault_folder_file": "user: vault files (FK folder->user)",
    "vault_folder_chunk": "user: vault embeddings (FK file->folder->user)",
    # --- operational logs / config (not user data, but not reference data) ---
    "pipeline_runs": "op-log: 46k-row pipeline run log, self-FK; inbound FKs "
    "(clearing_*.pipeline_run_id) are dropped to NULL on merge instead of "
    "dragging this across. No stable cross-env key.",
    "verification_runs": "op-log: SMTP batch log; email_ids is a JSON array of "
    "env-local discovered_email ids that cannot be safely remapped inside JSON.",
    "scoring_settings": "config: global scoring config (1 row). Prod may be "
    "independently tuned; merge manually if intended, do not auto-overwrite.",
    "alembic_version": "schema marker: must match (verified equal); never merged.",
}


# ─────────────────────────────────────────────────────────────────────────────
# Merge specification (declarative, one entry per MERGE table, in FK order)
# ─────────────────────────────────────────────────────────────────────────────

# on_unresolved policy for a remapped FK whose staging value can't be resolved
# to a prod parent: 'skip' (skip the whole child row + flag) or 'null' (write
# NULL — only valid for a nullable FK).
SKIP = "skip"
NULL = "null"

# On UPDATE, always keep prod's own value for these (never adopt staging's):
# created_at is prod's provenance; updated_at reflects prod's last real write.
PRESERVE_ON_UPDATE = {"created_at", "updated_at"}


@dataclass
class Remap:
    col: str
    parent: str
    on_unresolved: str = SKIP  # SKIP | NULL
    in_key: bool = False  # participates in the business key?


@dataclass
class MergeSpec:
    table: str
    # env-invariant natural key columns (e.g. crd_number, dedupe_key, cik):
    natural_key: list[str] = field(default_factory=list)
    remaps: list[Remap] = field(default_factory=list)
    # columns never written from source (forced NULL on insert / prod-kept on
    # update): cross-boundary user refs, dropped op-log links, etc.
    drop_cols: list[str] = field(default_factory=list)
    strategy: str = "keyed"  # keyed | keyed_ambiguous | append_natural
    # custom effective-key builder(row, parent_maps, side) -> tuple | None
    effective_key_fn: Callable | None = None
    flags: list[str] = field(default_factory=list)
    cast_cols: dict[str, str] = field(default_factory=dict)  # col -> pg type for insert

    @property
    def key_remap_cols(self) -> list[str]:
        return [r.col for r in self.remaps if r.in_key]


# --- custom effective-key builders for the two polymorphic tables ------------


def _cam_key(row, pmaps, side):
    """clearing_agency_memberships: XOR bd/advisor + agency."""
    agency = row["agency"]
    if row.get("broker_dealer_id") is not None:
        pid = row["broker_dealer_id"] if side == "target" else pmaps["broker_dealers"].get(row["broker_dealer_id"])
        return ("bd", pid, agency) if pid is not None else None
    if row.get("advisor_id") is not None:
        pid = row["advisor_id"] if side == "target" else pmaps["investment_advisors"].get(row["advisor_id"])
        return ("ia", pid, agency) if pid is not None else None
    return None


_EMB_PARENT = {
    "broker_dealer": "broker_dealers",
    "investment_advisor": "investment_advisors",
    "institutional_investor": "institutional_investors",
}


def _emb_key(row, pmaps, side):
    """chatbot_firm_embedding: (entity_type, entity_id remapped by type)."""
    et = row["entity_type"]
    eid = row["entity_id"]
    if side == "target":
        return (et, eid)
    parent = _EMB_PARENT.get(et)
    if parent is None:
        return None
    pid = pmaps[parent].get(eid)
    return (et, pid) if pid is not None else None


# --- the ordered merge plan (parents strictly before children) ---------------

MERGE_SPECS: list[MergeSpec] = [
    # ── roots ────────────────────────────────────────────────────────────────
    MergeSpec("broker_dealers", natural_key=["crd_number"],
              flags=["business key crd_number is NOT a DB unique constraint but "
                     "is unique+complete in both DBs (0 nulls/0 dups verified); "
                     "cik has nulls so is a weaker key."]),
    MergeSpec("investment_advisors", natural_key=["crd_number"],
              flags=["1 prod row has NULL crd_number+cik (unmatchable prod row, "
                     "left untouched); all staging rows have crd_number."]),
    MergeSpec("reporting_owners", natural_key=["cik"],
              flags=["form4_transactions references reporting_owners by CIK "
                     "(natural key), not by id -> no id remap needed downstream."]),
    MergeSpec("competitor_providers", natural_key=["name"]),
    # ── entities that depend on the roots ────────────────────────────────────
    MergeSpec("institutional_investors", natural_key=["cik"],
              remaps=[Remap("advisor_id", "investment_advisors", NULL)],
              flags=["name is non-unique (1 dup pair); cik used as key "
                     "(complete+unique in both DBs)."]),
    MergeSpec("executive_contacts", natural_key=["name", "title"],
              remaps=[Remap("bd_id", "broker_dealers", SKIP, in_key=True)],
              strategy="keyed_ambiguous",
              flags=["RISK: (bd_id,name,title) has 1 duplicate group in staging "
                     "(2 rows); apollo_person_id is mostly NULL so unusable as "
                     "key. Duplicate-key staging rows are skipped + reported."]),
    MergeSpec("advisor_contacts", natural_key=["name", "title"],
              remaps=[Remap("advisor_id", "investment_advisors", SKIP, in_key=True)]),
    MergeSpec("financial_metrics", natural_key=["report_date"],
              remaps=[Remap("bd_id", "broker_dealers", SKIP, in_key=True)]),
    MergeSpec("filing_alerts", natural_key=["dedupe_key"],
              remaps=[Remap("bd_id", "broker_dealers", SKIP)]),
    MergeSpec("industry_arrangements", natural_key=["kind"],
              remaps=[Remap("bd_id", "broker_dealers", SKIP, in_key=True)]),
    MergeSpec("introducing_arrangements",
              natural_key=["business_name", "effective_date"],
              remaps=[Remap("bd_id", "broker_dealers", SKIP, in_key=True)],
              strategy="keyed_ambiguous",
              flags=["RISK: table has NO unique constraint and NO reliable "
                     "natural key -- (bd_id,business_name,effective_date) has 6 "
                     "duplicate groups (~12 rows) in both DBs. Duplicate-key "
                     "rows are skipped + reported; review before --apply."]),
    MergeSpec("clearing_arrangements", natural_key=["filing_year"],
              remaps=[Remap("bd_id", "broker_dealers", SKIP, in_key=True)],
              drop_cols=["pipeline_run_id"],
              flags=["pipeline_run_id dropped to NULL on insert / prod-kept on "
                     "update (pipeline_runs is excluded op-log)."]),
    MergeSpec("clearing_agency_memberships", natural_key=["agency"],
              remaps=[Remap("broker_dealer_id", "broker_dealers", SKIP, in_key=True),
                      Remap("advisor_id", "investment_advisors", SKIP, in_key=True)],
              drop_cols=["pipeline_run_id"],
              effective_key_fn=_cam_key,
              flags=["polymorphic XOR (bd|advisor)+agency; pipeline_run_id "
                     "dropped (excluded op-log)."]),
    MergeSpec("advisor_filings", natural_key=["dedupe_key"],
              remaps=[Remap("advisor_id", "investment_advisors", SKIP, in_key=True)]),
    MergeSpec("form4_transactions", natural_key=["dedupe_key"]),
    MergeSpec("chatbot_firm_embedding", natural_key=["entity_type"],
              remaps=[Remap("entity_id", "__polymorphic__", SKIP, in_key=True)],
              effective_key_fn=_emb_key,
              cast_cols={"embedding": "vector"},
              flags=["entity_id is a SOFT (no-FK) reference to a firm id; "
                     "remapped by entity_type. embedding(vector) excluded from "
                     "change-fingerprint; content_hash is the change signal."]),
    MergeSpec("chatbot_learned_term", natural_key=["term_normalized"],
              drop_cols=["created_by_user_id"],
              flags=["global glossary; created_by_user_id (FK user) dropped to "
                     "NULL -- staging user id would not exist in prod."]),
    MergeSpec("clearing_partner_merge_suggestions", natural_key=["cluster_signature"],
              remaps=[Remap("accepted_provider_id", "competitor_providers", NULL)]),
    MergeSpec("investor_contacts", natural_key=["name", "title"],
              remaps=[Remap("investor_id", "institutional_investors", SKIP, in_key=True)]),
    MergeSpec("investor_filings", natural_key=["dedupe_key"],
              remaps=[Remap("investor_id", "institutional_investors", SKIP, in_key=True)]),
    # ── Tier-2: run-log subtree (ambiguous cross-env identity — flagged) ──────
    MergeSpec("extraction_run", natural_key=["pipeline_name", "domain", "created_at"],
              remaps=[Remap("bd_id", "broker_dealers", NULL),
                      Remap("advisor_id", "investment_advisors", NULL)],
              strategy="append_natural",
              flags=["TIER-2 op-log: cross-env identity is heuristic "
                     "(pipeline_name,domain,created_at -- unique in both DBs). "
                     "Parent of discovered_email; must merge to supply run_ids."]),
    MergeSpec("discovered_email", natural_key=["email"],
              remaps=[Remap("run_id", "extraction_run", SKIP, in_key=True),
                      Remap("bd_id", "broker_dealers", NULL),
                      Remap("advisor_id", "investment_advisors", NULL)],
              strategy="append_natural",
              flags=["TIER-2: key (run_id->remapped, email) inherits "
                     "extraction_run's heuristic identity."]),
    MergeSpec("email_verification", natural_key=["checked_at"],
              remaps=[Remap("discovered_email_id", "discovered_email", SKIP, in_key=True)],
              strategy="append_natural",
              flags=["TIER-2: discovered_email_id alone is non-unique (history "
                     "rows) -> (discovered_email_id->remapped, checked_at)."]),
]

MERGE_TABLE_NAMES = [s.table for s in MERGE_SPECS]


# ─────────────────────────────────────────────────────────────────────────────
# Introspection (target = source of truth for column lists / pk)
# ─────────────────────────────────────────────────────────────────────────────


def introspect(conn) -> dict:
    cols: dict[str, list[str]] = {}
    types: dict[str, dict] = {}
    pk: dict[str, list[str]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name, data_type, udt_name "
            "FROM information_schema.columns "
            "WHERE table_schema='public' ORDER BY table_name, ordinal_position"
        )
        for tn, cn, dt, udt in cur.fetchall():
            cols.setdefault(tn, []).append(cn)
            types.setdefault(tn, {})[cn] = {"data_type": dt, "udt": udt}
        cur.execute(
            """SELECT cl.relname, att.attname
               FROM pg_index i
               JOIN pg_class cl ON cl.oid=i.indrelid
               JOIN pg_namespace ns ON ns.oid=cl.relnamespace
               JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum,ord) ON true
               JOIN pg_attribute att ON att.attrelid=i.indrelid AND att.attnum=k.attnum
               WHERE ns.nspname='public' AND i.indisprimary
               ORDER BY cl.relname, k.ord"""
        )
        for tn, cn in cur.fetchall():
            pk.setdefault(tn, []).append(cn)
    return {"cols": cols, "pk": pk, "types": types}


def payload_cols(spec: MergeSpec, all_cols: list[str], pk: list[str]) -> list[str]:
    """Env-invariant business columns used for the change fingerprint."""
    exclude = set(pk) | {"created_at", "updated_at", "embedding"}
    exclude |= set(spec.natural_key) | set(spec.drop_cols)
    exclude |= {r.col for r in spec.remaps}
    return [c for c in all_cols if c not in exclude]


def fetch_cols(spec: MergeSpec, pk: list[str]) -> list[str]:
    """Columns needed to build keys + remaps (plus pk)."""
    need: list[str] = list(pk)
    for c in spec.natural_key:
        if c not in need:
            need.append(c)
    for r in spec.remaps:
        if r.col not in need:
            need.append(r.col)
    # polymorphic embedding needs entity_type+entity_id (entity_type already in
    # natural_key; entity_id is a remap col) -> covered.
    return need


# ─────────────────────────────────────────────────────────────────────────────
# Planning (dry-run) — builds the per-table plan + parent id-maps
# ─────────────────────────────────────────────────────────────────────────────


def fp_expr(cols: list[str]) -> sql.Composed:
    if not cols:
        return sql.SQL("md5('')")
    row = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
    return sql.SQL("md5(ROW({}) ::text)").format(row)


def fetch_side(conn, spec: MergeSpec, need: list[str], pcols: list[str]):
    selcols = list(dict.fromkeys(need))  # de-dupe, keep order
    select = sql.SQL(", ").join(sql.Identifier(c) for c in selcols)
    q = sql.SQL("SELECT {sel}, {fp} AS __fp FROM {tbl}").format(
        sel=select, fp=fp_expr(pcols), tbl=sql.Identifier(spec.table)
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(q)
        return cur.fetchall()


def effective_key(spec: MergeSpec, row: dict, pmaps: dict, side: str):
    if spec.effective_key_fn is not None:
        return spec.effective_key_fn(row, pmaps, side)
    parts: list = [row.get(c) for c in spec.natural_key]
    for r in spec.remaps:
        if not r.in_key:
            continue
        v = row.get(r.col)
        if side == "target":
            parts.append(v)
        else:
            mapped = pmaps.get(r.parent, {}).get(v) if v is not None else None
            if v is not None and mapped is None:
                return None  # unresolvable key parent -> not matchable/insertable-by-key
            parts.append(mapped)
    if any(p is None for p in parts):
        return None
    return tuple(parts)


@dataclass
class TablePlan:
    table: str
    source_rows: int = 0
    target_rows: int = 0
    inserts: int = 0
    updates: int = 0
    changed_upper: int = 0  # matched rows whose payload fingerprint differs
    ambiguous_skipped: int = 0
    unresolved_skipped: int = 0
    remap_stats: dict = field(default_factory=dict)  # col -> {existing,new,null,unresolved}
    warnings: list = field(default_factory=list)


def plan_table(spec: MergeSpec, src_rows, tgt_rows, pk, pmaps) -> tuple[TablePlan, dict, set]:
    plan = TablePlan(spec.table, len(src_rows), len(tgt_rows))
    pkcol = pk[0]
    changed_sids: set = set()  # matched staging ids whose payload fp differs

    # target index: effective_key -> (prod_id, fp)
    tgt_index: dict = {}
    tgt_dupes = 0
    for r in tgt_rows:
        k = effective_key(spec, r, pmaps, "target")
        if k is None:
            continue
        if k in tgt_index:
            tgt_dupes += 1
            continue
        tgt_index[k] = (r[pkcol], r["__fp"])
    if tgt_dupes:
        plan.warnings.append(f"{tgt_dupes} prod rows share a business key "
                             f"(prod-side non-unique); first kept for matching.")

    # remap accounting init
    for r in spec.remaps:
        plan.remap_stats[r.col] = {"existing": 0, "new": 0, "null": 0, "unresolved": 0}

    # walk source, dedupe by effective key
    seen_src_keys: dict = {}
    id_map: dict = {}  # staging_id -> prod_id | "NEW:table:sid"
    for r in sorted(src_rows, key=lambda x: x[pkcol]):
        sid = r[pkcol]

        # remap accounting (all remaps, key or not)
        row_skipped = False
        for rm in spec.remaps:
            if spec.effective_key_fn is _emb_key and rm.col == "entity_id":
                parent = _EMB_PARENT.get(r.get("entity_type"))
                v = r.get("entity_id")
                mapped = pmaps.get(parent, {}).get(v) if (parent and v is not None) else None
            else:
                v = r.get(rm.col)
                mapped = pmaps.get(rm.parent, {}).get(v) if v is not None else None
            st = plan.remap_stats[rm.col]
            if v is None:
                st["null"] += 1
            elif mapped is None:
                st["unresolved"] += 1
                if rm.on_unresolved == SKIP:
                    row_skipped = True
            elif isinstance(mapped, str) and mapped.startswith("NEW:"):
                st["new"] += 1
            else:
                st["existing"] += 1

        k = effective_key(spec, r, pmaps, "source")
        if row_skipped or k is None:
            plan.unresolved_skipped += 1
            continue
        if k in seen_src_keys:
            plan.ambiguous_skipped += 1
            continue
        seen_src_keys[k] = sid

        if k in tgt_index:
            prod_id, tgt_fp = tgt_index[k]
            id_map[sid] = prod_id
            plan.updates += 1
            if r["__fp"] != tgt_fp:
                plan.changed_upper += 1
                changed_sids.add(sid)
        else:
            id_map[sid] = f"NEW:{spec.table}:{sid}"
            plan.inserts += 1

    return plan, id_map, changed_sids


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────


def print_plan(plan: TablePlan, spec: MergeSpec, idx: int, total: int):
    keydesc = "+".join(spec.natural_key + [f"{r.col}->{r.parent}"
                                           for r in spec.remaps if r.in_key]) or "(custom)"
    if spec.effective_key_fn is _cam_key:
        keydesc = "(bd|advisor)->remap + agency"
    if spec.effective_key_fn is _emb_key:
        keydesc = "entity_type + entity_id->remap(by type)"
    print(f"\n[{idx:>2}/{total}] {plan.table}   key={keydesc}   strategy={spec.strategy}")
    print(f"        source={plan.source_rows}  target={plan.target_rows}")
    print(f"        INSERT={plan.inserts}  UPDATE={plan.updates}  "
          f"CHANGED(<=)={plan.changed_upper}  "
          f"ambiguous_skipped={plan.ambiguous_skipped}  "
          f"unresolved_skipped={plan.unresolved_skipped}")
    for col, st in plan.remap_stats.items():
        print(f"        remap {col}: existing={st['existing']} new_parent={st['new']} "
              f"null={st['null']} unresolved={st['unresolved']}")
    for f in spec.flags:
        print(f"        FLAG: {f}")
    for w in plan.warnings:
        print(f"        WARN: {w}")


# ─────────────────────────────────────────────────────────────────────────────
# Apply (single transaction) — NOT run in dry-run
# ─────────────────────────────────────────────────────────────────────────────


def apply_table(spec, src_conn, tgt_conn, id_map, pmaps, target_meta, fill_only,
                changed_sids):
    """Execute INSERT/UPDATE for one table inside the caller's open tgt txn.

    Catalog-driven binding is what makes this robust: json/jsonb values (read
    back from staging as Python dict/list) are wrapped in ``Json``/``Jsonb`` so
    psycopg3 can adapt them, and pgvector columns are read as text and re-cast
    with ``::vector`` on write. Uses ``executemany`` (pipelined) to keep the
    prod transaction — and its row locks — as short as possible. Every writable
    column uses a never-null-out ``COALESCE`` (staging fills/overwrites; a
    staging NULL keeps prod). ``pmaps`` NEW-sentinels are replaced with the real
    prod ids returned by INSERT so downstream children resolve correctly.
    """
    types = target_meta["types"][spec.table]
    all_cols = target_meta["cols"][spec.table]
    pkcol = target_meta["pk"][spec.table][0]
    write_cols = [c for c in all_cols if c != pkcol and c not in spec.drop_cols]
    jsonb_cols = {c for c in all_cols if types[c]["data_type"] == "jsonb"}
    json_cols = {c for c in all_cols if types[c]["data_type"] == "json"}
    vector_cols = {c for c in all_cols if types[c]["udt"] == "vector"}
    remap_by_col = {r.col: r for r in spec.remaps}

    # Only rows that actually need writing: all planned INSERTs, plus matched
    # rows whose payload fingerprint differs (changed_sids). Fingerprint-equal
    # matches are skipped — the COALESCE refresh would be a no-op. (Known edge:
    # a row whose ONLY difference is a remapped FK that prod has as NULL is
    # skipped too; remapped FKs are prod-wins on update, so this loses nothing
    # the merge would otherwise assert.)
    insert_cand = [s for s, t in id_map.items()
                   if isinstance(t, str) and t.startswith("NEW:")]
    update_cand = [(s, t) for s, t in id_map.items()
                   if not (isinstance(t, str) and t.startswith("NEW:"))
                   and s in changed_sids]
    skipped = sum(1 for t in id_map.values()
                  if not (isinstance(t, str) and t.startswith("NEW:"))) - len(update_cand)
    needed = insert_cand + [s for s, _ in update_cand]
    if not needed:
        return 0, 0, skipped

    # SELECT only the needed source rows; render pgvector cols as text for
    # deterministic rebind.
    sel_items = []
    for c in all_cols:
        if c in vector_cols:
            sel_items.append(sql.SQL("{c}::text AS {c}").format(c=sql.Identifier(c)))
        else:
            sel_items.append(sql.Identifier(c))
    with src_conn.cursor(row_factory=dict_row) as scur:
        scur.execute(sql.SQL("SELECT {} FROM {} WHERE {} = ANY(%s)").format(
            sql.SQL(", ").join(sel_items), sql.Identifier(spec.table),
            sql.Identifier(pkcol)), (needed,))
        src = {r[pkcol]: r for r in scur.fetchall()}

    def remapped(col, row):
        v = row.get(col)
        if v is None:
            return None
        if spec.effective_key_fn is _emb_key and col == "entity_id":
            parent = _EMB_PARENT.get(row.get("entity_type"))
            v = pmaps.get(parent, {}).get(v)
        else:
            v = pmaps.get(remap_by_col[col].parent, {}).get(v)
        return None if (isinstance(v, str) and v.startswith("NEW:")) else v

    def value_for(col, row):
        return remapped(col, row) if col in remap_by_col else row.get(col)

    def bind(col, v):
        if v is None:
            return None
        if col in jsonb_cols:
            return Jsonb(v)
        if col in json_cols:
            return Json(v)
        return v  # vector-as-text, scalars, real arrays (psycopg adapts lists)

    def ph(col):
        return (sql.SQL("{}::vector").format(sql.Placeholder())
                if col in vector_cols else sql.Placeholder())

    insert_sids = [s for s in insert_cand if s in src]
    update_pairs = [(s, t) for s, t in update_cand if s in src]
    inserted = updated = 0

    # ── INSERTs (executemany + RETURNING to capture prod ids, in order) ──
    if insert_sids:
        q = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
            sql.Identifier(spec.table),
            sql.SQL(", ").join(sql.Identifier(c) for c in write_cols),
            sql.SQL(", ").join(ph(c) for c in write_cols),
            sql.Identifier(pkcol),
        )
        seq = [[bind(c, value_for(c, src[s])) for c in write_cols] for s in insert_sids]
        with tgt_conn.cursor() as cur:
            cur.executemany(q, seq, returning=True)
            new_ids: list = []
            while True:
                rec = cur.fetchone()
                new_ids.append(rec[0] if rec else None)
                if not cur.nextset():
                    break
        for s, nid in zip(insert_sids, new_ids):
            id_map[s] = nid
            pmaps.setdefault(spec.table, {})[s] = nid
        inserted = len(insert_sids)

    # ── UPDATEs (executemany; never null-out prod values) ──
    if update_pairs:
        set_cols = [c for c in write_cols if c not in spec.natural_key]
        assigns = []
        for c in set_cols:
            keep_prod = c in remap_by_col or fill_only or c in PRESERVE_ON_UPDATE
            if keep_prod:
                assigns.append(sql.SQL("{col}=COALESCE({tbl}.{col}, {ph})").format(
                    col=sql.Identifier(c), tbl=sql.Identifier(spec.table), ph=ph(c)))
            else:
                assigns.append(sql.SQL("{col}=COALESCE({ph}, {tbl}.{col})").format(
                    col=sql.Identifier(c), ph=ph(c), tbl=sql.Identifier(spec.table)))
        q = sql.SQL("UPDATE {} SET {} WHERE {}={}").format(
            sql.Identifier(spec.table), sql.SQL(", ").join(assigns),
            sql.Identifier(pkcol), sql.Placeholder())
        seq = []
        for s, tid in update_pairs:
            row = src[s]
            vals = [bind(c, value_for(c, row)) for c in set_cols]
            vals.append(tid)
            seq.append(vals)
        with tgt_conn.cursor() as cur:
            cur.executemany(q, seq)
        updated = len(update_pairs)

    return inserted, updated, skipped


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def build_parent_map_from_plan(id_map: dict) -> dict:
    return dict(id_map)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-url", default=os.environ.get("DATABASE_URL_BACKEND_STAGING", ""))
    ap.add_argument("--target-url", default=os.environ.get("DATABASE_URL_BACKEND", ""))
    ap.add_argument("--apply", action="store_true",
                    help="perform writes in ONE transaction (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="force dry-run (default)")
    ap.add_argument("--fill-only", action="store_true",
                    help="prod always wins; only NULL prod columns are filled")
    ap.add_argument("--i-understand-this-writes-to-prod", action="store_true",
                    help="required safety ack for --apply")
    ap.add_argument("--validate-apply", action="store_true",
                    help="exercise the FULL write path against the target inside "
                         "one transaction, then ROLLBACK (real writes, nothing "
                         "persisted). Proves adaptation + remap before a real apply.")
    ap.add_argument("--tables", default="",
                    help="comma-separated subset of MERGE tables (default: all)")
    args = ap.parse_args()

    validate = args.validate_apply
    do_apply = args.apply and not args.dry_run and not validate
    dry_run = not (validate or do_apply)
    if do_apply and not args.i_understand_this_writes_to_prod:
        print("ERROR: --apply requires --i-understand-this-writes-to-prod", file=sys.stderr)
        return 2
    if not args.source_url or not args.target_url:
        print("ERROR: source/target URL missing (flags or env).", file=sys.stderr)
        return 2

    src_dsn = normalize_dsn(args.source_url)
    tgt_dsn = normalize_dsn(args.target_url)
    subset = {t.strip() for t in args.tables.split(",") if t.strip()}
    specs = [s for s in MERGE_SPECS if not subset or s.table in subset]

    if validate:
        mode = "VALIDATE-APPLY (real writes, then ROLLBACK — nothing persisted)"
    elif do_apply:
        mode = "APPLY (single transaction, COMMIT)"
    else:
        mode = "DRY-RUN (zero writes)"
    policy = "fill-only (prod wins; fill NULLs)" if args.fill_only \
        else "staging-wins-never-null (COALESCE(staging, prod))"
    print("=" * 78)
    print(f"replicate_staging_to_prod — {mode}")
    print(f"column policy: {policy}")
    print(f"merge tables: {len(specs)} | excluded tables: {len(EXCLUDE_TABLES)}")
    print("=" * 78)

    try:
        # Source is autocommit: reads were already per-statement snapshots under
        # READ COMMITTED, and autocommit means the connection is never idle *in
        # transaction* while a long target write runs (staging's server-side
        # idle_in_transaction_session_timeout killed the 2026-07-02 rehearsal).
        src_conn = psycopg.connect(src_dsn, connect_timeout=30, autocommit=True)
        tgt_conn = psycopg.connect(tgt_dsn, connect_timeout=30, autocommit=False)
    except Exception as exc:  # noqa: BLE001
        print("connect failed: " + scrub(str(exc), src_dsn, tgt_dsn), file=sys.stderr)
        return 2

    rc = 0
    try:
        src_conn.read_only = True
        if dry_run:
            tgt_conn.read_only = True  # triple safety: target is read-only in dry-run
        for c in (src_conn, tgt_conn):
            with c.cursor() as cur:
                cur.execute("SET TIME ZONE 'UTC'")  # stable fingerprints
        if not dry_run:
            # Reliability guardrails on the LIVE target: fail fast rather than
            # block prod writers, and never leave a long idle-in-transaction lock.
            with tgt_conn.cursor() as cur:
                cur.execute("SET statement_timeout = '600s'")
                cur.execute("SET lock_timeout = '30s'")
                cur.execute("SET idle_in_transaction_session_timeout = '900s'")

        src_meta = introspect(src_conn)
        tgt_meta = introspect(tgt_conn)

        # schema parity guard
        drift = []
        for s in specs:
            sc, tc = set(src_meta["cols"].get(s.table, [])), set(tgt_meta["cols"].get(s.table, []))
            if sc != tc:
                drift.append(f"{s.table}: only_src={sorted(sc-tc)} only_tgt={sorted(tc-sc)}")
        if drift:
            print("ABORT: source/target schema drift on merge tables:")
            for d in drift:
                print("  " + d)
            return 3

        pmaps: dict = {}
        plans: list = []
        grand = {"inserts": 0, "updates": 0, "changed": 0, "amb": 0, "unres": 0}

        for i, spec in enumerate(specs, 1):
            pk = tgt_meta["pk"][spec.table]
            need = fetch_cols(spec, pk)
            pcols = payload_cols(spec, tgt_meta["cols"][spec.table], pk)
            src_rows = fetch_side(src_conn, spec, need, pcols)
            tgt_rows = fetch_side(tgt_conn, spec, need, pcols)
            plan, id_map, changed_sids = plan_table(spec, src_rows, tgt_rows, pk, pmaps)
            pmaps[spec.table] = build_parent_map_from_plan(id_map)
            print_plan(plan, spec, i, len(specs))
            plans.append((spec, plan, id_map, changed_sids))
            grand["inserts"] += plan.inserts
            grand["updates"] += plan.updates
            grand["changed"] += plan.changed_upper
            grand["amb"] += plan.ambiguous_skipped
            grand["unres"] += plan.unresolved_skipped

        print("\n" + "=" * 78)
        print("TOTALS")
        print(f"  rows to INSERT : {grand['inserts']}")
        print(f"  rows to UPDATE : {grand['updates']}  (of which <= {grand['changed']} "
              f"actually differ; rest are no-op refreshes)")
        print(f"  ambiguous skipped (dup business key in staging): {grand['amb']}")
        print(f"  unresolved skipped (unmappable required parent) : {grand['unres']}")
        print("=" * 78)

        if dry_run:
            print("\nDRY-RUN complete. No rows were written. "
                  "Review the plan, then re-run with --apply to commit.")
        else:
            label = "VALIDATE-APPLY" if validate else "APPLY"
            print(f"\n{label}: exercising the full write path in ONE transaction ...")
            inserted = updated = 0
            failures = 0
            t0 = time.monotonic()
            for spec, plan, id_map, changed_sids in plans:
                ts = time.monotonic()
                try:
                    ins, upd, skp = apply_table(spec, src_conn, tgt_conn, id_map, pmaps,
                                                tgt_meta, args.fill_only, changed_sids)
                except Exception as exc:  # noqa: BLE001 — surface the failing table
                    failures += 1
                    print(f"  [{spec.table}] WRITE FAILED -> "
                          f"{scrub(str(exc), src_dsn, tgt_dsn)}")
                    raise
                inserted += ins
                updated += upd
                print(f"  [{spec.table:<34}] +{ins:>5} inserted  ~{upd:>6} updated  "
                      f"{skp:>6} unchanged-skipped  ({time.monotonic() - ts:0.1f}s)")
            elapsed = time.monotonic() - t0
            if validate:
                tgt_conn.rollback()
                print("\n" + "=" * 78)
                print(f"VALIDATION PASSED — 0 errors across {len(plans)} tables. "
                      f"Exercised {inserted} inserts + {updated} updates in "
                      f"{elapsed:0.1f}s, then ROLLED BACK.")
                print("Zero table rows persisted. (Identity sequences advanced by "
                      "the exercised inserts — non-transactional — which is "
                      "harmless: it only leaves gaps in the id space.)")
                print("Write path is clean and ready for the real --apply.")
                print("=" * 78)
            else:
                tgt_conn.commit()
                print(f"\nCOMMITTED. inserted={inserted} updated={updated} "
                      f"in {elapsed:0.1f}s.")
    except Exception as exc:  # noqa: BLE001
        try:
            tgt_conn.rollback()
        except Exception:
            pass
        print("ERROR: " + scrub(str(exc), src_dsn, tgt_dsn), file=sys.stderr)
        rc = 1
    finally:
        for c in (src_conn, tgt_conn):
            try:
                c.close()
            except Exception:
                pass
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
