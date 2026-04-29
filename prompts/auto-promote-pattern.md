# Auto-promote pattern — feature → develop → main in one paste

Default workflow for every cli01 / cli02 prompt going forward. Replaces the old "ship to develop, write a separate release prompt later" pattern. One paste now ships to prod.

**Adopted:** 2026-04-28 per Arvin's request after Sprint 1 + types-of-business filter shipped this way organically.

---

## What every prompt should include

Two merge phases inside the same workflow, with halts between them.

### Phase A — Feature merge to develop

```
STEP X — Open the feature PR (against develop)
    git push -u origin <branch>
    gh pr create --base develop --head <branch> \
      --title "..." --body-file <path>

    # AI-attribution scrub:
    gh pr view --json body --jq '.body' | \
        grep -iE "claude|anthropic|generated with|co-authored-by" \
        && { echo "AI trailer in PR body — STOP and edit."; exit 1; } || true

STEP X+1 — Wait for CI green, then squash-merge to develop
    gh pr checks --watch
    gh pr merge --squash --delete-branch --admin
    FEATURE_PR_NUM=<captured>
    FEATURE_SHA=$(gh pr view "$FEATURE_PR_NUM" --json mergeCommit --jq '.mergeCommit.oid')
```

### Phase B — Auto-promote develop → main (with safety guards)

```
STEP X+2 — Migration safety check (HALT if migration present)
    NEW_MIGRATIONS=$(git diff --name-only origin/main..origin/develop \
        -- backend/alembic/versions/)
    if [ -n "$NEW_MIGRATIONS" ]; then
        echo "===================================================="
        echo "HALT: this release would ship NEW MIGRATIONS to prod"
        echo "----------------------------------------------------"
        echo "$NEW_MIGRATIONS"
        echo "----------------------------------------------------"
        echo "Migrations require a deliberate decision (they run on"
        echo "cold start of the prod backend revision). Confirm with"
        echo "Arvin before continuing. To proceed anyway, manually"
        echo "open the release PR per Phase B below."
        echo "===================================================="
        exit 0
    fi

STEP X+3 — Open develop → main release PR
    git fetch origin --prune
    COMMITS_AHEAD=$(git rev-list --count origin/main..origin/develop)
    if [ "$COMMITS_AHEAD" -eq 0 ]; then
        echo "develop is already at main. Nothing to release."
        exit 0
    fi

    BODY_FILE=$(mktemp)
    {
        echo "## Promoted to production"
        echo
        git log origin/main..origin/develop --format='- %s' --reverse
        echo
        echo "## Why this release"
        echo
        echo "<one-line summary of the cargo from this prompt's task>"
        echo
        echo "## Schema changes"
        echo
        echo "None."   # Phase A would have halted if there were any
        echo
        echo "## Rollback"
        echo
        echo 'Each Cloud Run revision is immutable:'
        echo '```'
        echo 'gcloud run services update-traffic <fis-backend|fis-frontend> \'
        echo '  --region=us-central1 --project=fis-lead-gen \'
        echo '  --to-revisions=<LAST_GOOD_REV>=100'
        echo '```'
    } > "$BODY_FILE"

    gh pr create --base main --head develop \
        --title "Promote: <one-line summary> to production" \
        --body-file "$BODY_FILE"

    RELEASE_PR=$(gh pr list --base main --head develop --state open \
        --json number --jq '.[0].number')

    # AI-attribution scrub on release PR:
    gh pr view "$RELEASE_PR" --json body --jq '.body' | \
        grep -iE "claude|anthropic|generated with|co-authored-by" \
        && { echo "AI trailer in release PR body — STOP and edit."; exit 1; } || true

STEP X+4 — Merge with --merge --admin
    gh pr merge "$RELEASE_PR" --merge --admin
    # Do NOT pass --delete-branch. develop is long-lived.

STEP X+5 — Watch the prod deploy
    gh run list --branch main --limit 3
    LATEST_RUN=$(gh run list --branch main --limit 1 --json databaseId \
        --jq '.[0].databaseId')
    gh run watch "$LATEST_RUN"

STEP X+6 — Smoke check + capture prod revisions
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
        https://fis.alchemydev.io/api/backend/api/v1/health)
    BACKEND_REV=$(gcloud run services describe fis-backend \
        --region=us-central1 --project=fis-lead-gen \
        --format='value(status.latestReadyRevisionName)' 2>/dev/null)
    FRONTEND_REV=$(gcloud run services describe fis-frontend \
        --region=us-central1 --project=fis-lead-gen \
        --format='value(status.latestReadyRevisionName)' 2>/dev/null)
```

### Final report (≤ 5 lines, captures the audit trail)

The chat reply at the end of every prompt should include:

- Feature PR # + merge SHA (Phase A)
- Release PR # + merge SHA (Phase B)
- Prod deploy status
- Backend + frontend revision names (audit trail)
- Smoke `/api/v1/health` status code

---

## Safety guards (always present)

1. **MIGRATION-BEARING PRs SHIP ATOMICALLY.** Phase A and Phase B run in the SAME paste. No halt between them. **Why:** staging and prod share a Neon DB until task #4 splits them. When Phase A merges to develop, staging auto-deploys and applies the migration to the shared DB. If Phase B is delayed (queue, halt, manual gate), prod is running stale code that doesn't know about the migration — when prod's container next cold-starts, it crash-loops on alembic version mismatch. Three production outages in five days from this exact pattern (2026-04-24, 2026-04-28, 2026-04-29). The human review of the migration content happens BEFORE pasting; the act of pasting commits to atomic ship. The migration content is printed in Phase A's PR body so the operator can review pre-paste.
2. **HALT if CI is not green** on the develop merge (admin override allowed when CI is queue-blocked but tests pass locally — see incident-prod-migration-drift-2026-04-29.md).
3. **HALT if AI-attribution scrub fails** on either PR body.
4. **HALT if `gcloud auth` is expired** for the prod-revision capture step. Fall back gracefully and flag it in the chat reply.
5. **Capture prod revision names** in the chat reply so every release leaves a one-line audit trail.
6. **Phase B coordination (when running parallel CLIs).** Before opening the release PR, check if one already exists for `develop → main`. If found, your Phase A commits are already on develop and will ship in the existing release PR — exit Phase B gracefully with a chat message naming the existing release PR. This prevents `gh pr create` errors and avoids duplicate release PRs racing each other:

```bash
EXISTING_RELEASE_PR=$(gh pr list --base main --head develop --state open \
    --json number --jq '.[0].number // empty')
if [ -n "$EXISTING_RELEASE_PR" ]; then
    echo "Release PR #$EXISTING_RELEASE_PR is already open from a parallel CLI."
    echo "This CLI's Phase A commits are already on develop and will ship in"
    echo "PR #$EXISTING_RELEASE_PR when it merges. Skipping Phase B."
    exit 0
fi
```

7. **Revision-id reservation for parallel BE CLIs with migrations.** When the orchestrator (or human dispatcher) sends two or more parallel BE CLIs in the same batch and ANY of them might author a migration, **each brief must hand its CLI a unique pre-reserved `YYYYMMDD_NNNN` revision id**. Do NOT let parallel CLIs both call `alembic revision --autogenerate` and reach for the next number — they will both pick the same one, and `develop` immediately enters a dual-head state that crashes `alembic upgrade head` on prod cold start. Caused the 2026-04-29 scoring/drop-safety-net dual-`0020` incident (`reports/incident-scoring-migration-collision-2026-04-29.md`).

   Pattern in the brief:

   ```
   STEP X — Author Alembic migration
       cd backend
       # PRE-RESERVED revision id for this batch — do NOT auto-pick:
       alembic revision -m "..." --rev-id 20260429_0021
       cd ..
   ```

   Reserve slots in the batch dispatch message: cli01 → 0020, cli03 → 0021, etc.

8. **Hotfix coordination after a migration collision.** If a dual-head collision DOES happen, only ONE party renumbers (the second-in-time CLI). The first-in CLI's migration keeps its slot. Both parties trying to "renumber my own migration" simultaneously creates a dual-tail state that's worse than the original collision (both files claim the same NEW slot, parent slot orphaned). This was the secondary failure mode in the 2026-04-29 incident.

9. **Smoke checks for data migrations must inspect the actual row.** `alembic stamp <rev>` is silent — version_num can advance past a data migration without that migration's `op.execute(UPDATE ...)` running. The 2026-04-29 incident shipped scoring code with the OLD weights live in prod for ~10 minutes after the deploy was healthy because the smoke check only verified `alembic_version`, not the row. Pattern:

   ```python
   # After deploy, in addition to /api/v1/health and alembic_version:
   row = c.execute(text("SELECT * FROM <table> WHERE ...")).first()
   assert row.<column> == <expected_post_migration_value>, (
       "Data migration silently skipped — manual UPDATE may be needed."
   )
   ```

---

## Migration-PR pattern (interim until task #4 lands)

Until staging and prod have separate Neon DBs, treat any PR that adds files to `backend/alembic/versions/` as a special case:

- Phase A and Phase B run in the same prompt paste, sequentially.
- The build prompt prints the migration content (file path + SQL) in the PR body of Phase A.
- The operator reads the PR body BEFORE pasting; that's the review gate.
- Phase B does NOT halt on migrations. It opens the release PR, merges with `--merge --admin`, watches the prod deploy, smokes `/api/v1/health`, and verifies the migration ran.
- If GH Actions runners are queue-blocked, fall back to manual `gcloud builds submit` + `gcloud run deploy` per the incident playbook in `reports/incident-prod-migration-drift-2026-04-29.md`. Use Cloud Build (not local docker) for portability.

After task #4 ships (split staging/prod DBs), this special case goes away — staging migrations no longer affect prod, and the original halt-on-migration safety can return.

---

## When to deviate from the default

Skip Phase B (don't auto-promote) when ANY of:

- The change touches authentication, secrets, or session handling (high blast radius).
- The change is a feature flag or experiment that should soak on staging first.
- Arvin explicitly says "ship to develop only" in the message that authorized the work.

In those cases the prompt should explicitly say at the top:

> **Auto-promote: NO. Ship to develop only. Release to main is a separate manual decision.**

This makes the deviation visible at a glance.

**Note:** migration-bearing PRs are no longer in this list. Per the rule above, migrations MUST go through atomic Phase A + Phase B in one paste until the DB split lands.

---

## Why this exists

The original workflow shipped to develop, then required a separate "release prompt" to promote to main. That added 5–10 min of CLI ceremony per release for a one-person reviewing/approving cadence (Arvin) where the soak time wasn't being used. After Sprint 1 + the types-of-business filter shipped via the auto-promote pattern organically without incident, Arvin asked to make it the default.

The migration-PR atomic-ship rule was added 2026-04-29 after the third migration-drift outage in five days. Until staging and prod have separate Neon DBs (task #4 — currently deferred), every minute between staging migration apply and prod deploy is an open outage window. Atomic shipping shrinks that window from "hours of CI queue + manual gate" to "seconds of build + deploy time."

Rules #7-#9 were added 2026-04-29 (later that day) after the fourth migration incident — a revision-id collision between parallel CLI prompts that both autogenerated `20260429_0020` simultaneously (`reports/incident-scoring-migration-collision-2026-04-29.md`). That incident also re-opened the question of whether deferring task #4 was the right call: four migration-related prod incidents in five days share a common antecedent (shared staging+prod Neon DB), and atomic-ship discipline alone has not been sufficient to prevent them. The lessons captured in rules #7-#9 above mitigate the *parallel-CLI* failure mode, but the underlying single-DB structural issue remains.
