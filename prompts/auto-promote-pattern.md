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

1. **HALT on new migrations.** Migrations to prod without a soak window are the highest-risk change. If Phase A produced new files in `backend/alembic/versions/`, the prompt stops before opening the release PR and waits for explicit human confirmation. (Documents it clearly so Arvin can manually proceed if intended.)
2. **HALT if CI is not green** on the develop merge.
3. **HALT if AI-attribution scrub fails** on either PR body.
4. **HALT if `gcloud auth` is expired.** The prod-revision capture in Phase B needs gcloud — fall back gracefully and flag it in the chat reply if the auth window expired mid-run.
5. **Capture prod revision names** in the chat reply so every release leaves a one-line audit trail.

---

## When to deviate from the default

Skip Phase B (don't auto-promote) when ANY of:

- The change includes a database migration (Phase A halts automatically — confirm with Arvin first).
- The change touches authentication, secrets, or session handling (high blast radius).
- The change is a feature flag or experiment that should soak on staging first.
- Arvin explicitly says "ship to develop only" in the message that authorized the work.

In those cases the prompt should explicitly say at the top:

> **Auto-promote: NO. Ship to develop only. Release to main is a separate manual decision.**

This makes the deviation visible at a glance.

---

## Why this exists

The original workflow shipped to develop, then required a separate "release prompt" to promote to main. That added 5–10 min of CLI ceremony per release for a one-person reviewing/approving cadence (Arvin) where the soak time wasn't being used. After Sprint 1 + the types-of-business filter shipped via the auto-promote pattern organically without incident, Arvin asked to make it the default. Migrations remain a hard halt because they're the one class of change where the soak window genuinely catches problems we can't detect in CI.
