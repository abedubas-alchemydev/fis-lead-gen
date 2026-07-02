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
    conn = psycopg.connect(dsn, connect_timeout=30, autocommit=False, row_factory=dict_row)
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

    # ── re-point children row by row; delete child on unique conflict ──
    moved = {}
    deleted_children = {}
    for tbl, col in fks:
        cur.execute(sql.SQL("SELECT id, {c} AS ref FROM {t} WHERE {c} = ANY(%s)").format(
            c=sql.Identifier(col), t=sql.Identifier(tbl)), (dupe_ids,))
        rows = cur.fetchall()
        m = dc = 0
        for r in rows:
            try:
                with conn.transaction():  # savepoint per row
                    cur.execute(sql.SQL("UPDATE {t} SET {c} = %s WHERE id = %s").format(
                        t=sql.Identifier(tbl), c=sql.Identifier(col)), (pairs[r["ref"]], r["id"]))
                m += 1
            except psycopg.errors.UniqueViolation:
                cur.execute(sql.SQL("DELETE FROM {t} WHERE id = %s").format(
                    t=sql.Identifier(tbl)), (r["id"],))
                dc += 1
        if rows:
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
