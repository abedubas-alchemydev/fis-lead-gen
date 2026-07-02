#!/usr/bin/env python3
"""One-off: remove the 2026-07-02 initial-load CIK-padding duplicates from prod.

A duplicate = the NEWER row of a (crd_number) pair (the padded-CIK insert).
Plan per pair: (1) fill-only merge of harvest scalars dupe->original,
(2) re-point every FK child row dupe->original (delete the child instead when
re-pointing would violate a unique constraint — the original already has it),
(3) delete the dupe row. Everything runs in ONE transaction; a full JSONL
backup of dupe rows + their children is written BEFORE any write.

Dry-run by default; --apply to commit. DATABASE_URL from env.
"""
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

APPLY = "--apply" in sys.argv
BACKUP = sys.argv[sys.argv.index("--backup") + 1] if "--backup" in sys.argv else "bd_dupe_backup.jsonl"

# fill-only scalar merge: original keeps its value; dupe fills NULLs.
FILL_COLS = [
    "sec_file_number", "name", "city", "state", "status", "branch_count",
    "business_type", "matched_source", "last_filing_date", "filings_index_url",
    "website", "website_source", "types_of_business", "direct_owners",
    "executive_officers", "firm_operations_text", "dba_names",
]

def jdef(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(str(type(o)))

def main() -> int:
    dsn = (os.environ["DATABASE_URL"]
           .replace("postgresql+psycopg://", "postgresql://")
           .replace("postgresql+asyncpg://", "postgresql://"))
    conn = psycopg.connect(
        dsn, connect_timeout=30, autocommit=False, row_factory=dict_row,
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
        options="-c idle_in_transaction_session_timeout=0 -c statement_timeout=120000",
    )
    cur = conn.cursor()
    # Dry-run exercises the FULL write path and rolls back at the end
    # (same pattern as replicate_staging_to_prod --validate-apply).

    # dupe -> original id map (dupe = every non-min id in a duplicated CRD group)
    cur.execute("""
        SELECT bd.id AS dupe_id,
               (SELECT min(b2.id) FROM broker_dealers b2 WHERE b2.crd_number = bd.crd_number) AS orig_id
        FROM broker_dealers bd
        WHERE bd.crd_number IN (
            SELECT crd_number FROM broker_dealers WHERE crd_number IS NOT NULL
            GROUP BY 1 HAVING count(*) > 1)
          AND bd.id <> (SELECT min(b3.id) FROM broker_dealers b3 WHERE b3.crd_number = bd.crd_number)
        ORDER BY bd.id""")
    pairs = {r["dupe_id"]: r["orig_id"] for r in cur.fetchall()}
    print(f"duplicate pairs: {len(pairs)}")
    if not pairs:
        return 0
    dupe_ids = list(pairs.keys())
    orig_ids = [pairs[d] for d in dupe_ids]  # positionally aligned with dupe_ids

    # discover every FK column referencing broker_dealers.id
    cur.execute("""
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu USING (constraint_name, table_schema)
        JOIN information_schema.constraint_column_usage ccu USING (constraint_name, table_schema)
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
          AND ccu.table_name = 'broker_dealers' AND ccu.column_name = 'id'""")
    fks = [(r["table_name"], r["column_name"]) for r in cur.fetchall()]
    # soft (no-FK) reference:
    soft = [("chatbot_firm_embedding", "entity_id", "entity_type = 'broker_dealer'")]
    print("FK children:", fks)

    # ── backup (always, even in dry-run: it is read-only) ──
    with open(BACKUP, "w") as f:
        cur.execute("SELECT * FROM broker_dealers WHERE id = ANY(%s)", (dupe_ids,))
        for row in cur.fetchall():
            f.write(json.dumps({"table": "broker_dealers", "row": row}, default=jdef) + "\n")
        for tbl, col in fks + [(t, c) for t, c, _ in soft]:
            cond = "" if (tbl, col) not in [(t, c) for t, c, _ in soft] else " AND entity_type='broker_dealer'"
            cur.execute(
                sql.SQL("SELECT * FROM {} WHERE {} = ANY(%s)" + cond).format(
                    sql.Identifier(tbl), sql.Identifier(col)), (dupe_ids,))
            for row in cur.fetchall():
                f.write(json.dumps({"table": tbl, "row": row}, default=jdef) + "\n")
    print(f"backup written: {BACKUP}")

    # ── fill-only scalar merge into originals ──
    assigns = sql.SQL(", ").join(
        sql.SQL("{c} = COALESCE(o.{c}, d.{c})").format(c=sql.Identifier(c)) for c in FILL_COLS)
    cur.execute(sql.SQL("""
        UPDATE broker_dealers o SET {assigns}
        FROM broker_dealers d
        WHERE d.id = ANY(%s) AND o.id = (
            SELECT min(b2.id) FROM broker_dealers b2 WHERE b2.crd_number = d.crd_number)
        """).format(assigns=assigns), (dupe_ids,))
    print(f"originals scalar-merged (fill-only): {cur.rowcount}")

    # ── re-point children set-based: per-table, one DELETE for each unique
    #    index that includes the FK column (drop the dupe's child where the
    #    original already holds that key), then one bulk UPDATE re-pointing the
    #    survivors. Set-based keeps the whole write to a handful of statements —
    #    the old row-by-row loop held the transaction open across ~1900
    #    round-trips and a transient Neon SSL drop aborted a prod apply
    #    (2026-07-02). Semantics are identical: every duplicated CRD group has
    #    exactly two rows, so each dupe maps to a unique original and two
    #    re-pointed children can never collide with each other — only with an
    #    original's existing child, which the EXISTS anti-join removes.
    moved = {}
    deleted_children = {}
    for tbl, col in fks:
        # unique indexes on this table whose columns include the FK column;
        # re-pointing (which changes only that column) can newly violate one of
        # these and nothing else. Skip expression indexes (attnum 0).
        cur.execute("""
            SELECT array_agg(a.attname ORDER BY k.ord) AS cols
            FROM pg_index ix
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = k.attnum
            WHERE t.relname = %s AND t.relnamespace = 'public'::regnamespace
              AND ix.indisunique AND 0 <> ALL (ix.indkey)
            GROUP BY ix.indexrelid
            HAVING %s = ANY(array_agg(a.attname))
        """, (tbl, col))
        uniq_indexes = [r["cols"] for r in cur.fetchall()]

        dc = 0
        for cols in uniq_indexes:
            others = [c for c in cols if c != col]
            match = (sql.SQL(" AND ").join(
                sql.SQL("o.{c} = d.{c}").format(c=sql.Identifier(c)) for c in others)
                if others else sql.SQL("true"))
            cur.execute(sql.SQL("""
                WITH m(dupe, orig) AS (SELECT * FROM unnest(%s::bigint[], %s::bigint[]))
                DELETE FROM {t} d USING m
                WHERE d.{c} = m.dupe
                  AND EXISTS (SELECT 1 FROM {t} o WHERE o.{c} = m.orig AND {match})
            """).format(t=sql.Identifier(tbl), c=sql.Identifier(col), match=match),
                (dupe_ids, orig_ids))
            dc += cur.rowcount

        cur.execute(sql.SQL("""
            WITH m(dupe, orig) AS (SELECT * FROM unnest(%s::bigint[], %s::bigint[]))
            UPDATE {t} d SET {c} = m.orig FROM m WHERE d.{c} = m.dupe
        """).format(t=sql.Identifier(tbl), c=sql.Identifier(col)), (dupe_ids, orig_ids))
        m = cur.rowcount

        if m or dc:
            moved[tbl], deleted_children[tbl] = m, dc
    # soft refs: original already has its own embedding -> just delete dupes'
    cur.execute("DELETE FROM chatbot_firm_embedding WHERE entity_type='broker_dealer' AND entity_id = ANY(%s)", (dupe_ids,))
    emb_deleted = cur.rowcount
    print("children re-pointed:", moved)
    print("children deleted (dedupe conflicts):", deleted_children, "| dupe embeddings deleted:", emb_deleted)

    # ── delete the dupes ──
    cur.execute("DELETE FROM broker_dealers WHERE id = ANY(%s)", (dupe_ids,))
    print(f"dupe broker_dealers deleted: {cur.rowcount}")

    # ── verify ──
    cur.execute("SELECT count(*) AS n FROM broker_dealers")
    total = cur.fetchone()["n"]
    cur.execute("""SELECT count(*) AS n FROM (SELECT crd_number FROM broker_dealers
                   WHERE crd_number IS NOT NULL GROUP BY 1 HAVING count(*)>1) t""")
    remaining = cur.fetchone()["n"]
    print(f"VERIFY: total={total} remaining_dup_groups={remaining}")

    if APPLY and remaining == 0:
        conn.commit()
        print("COMMITTED.")
    else:
        conn.rollback()
        print("DRY-RUN (rolled back)." if not APPLY else "ROLLED BACK: dup groups remain!")
        return 0 if not APPLY else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
