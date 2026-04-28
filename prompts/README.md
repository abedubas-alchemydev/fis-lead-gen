# CC CLI Prompt Templates

Templates for feeding parallel Claude Code CLI sessions in three git worktrees. The goal is **all terminals always productive, never idle**.

## The three worktrees

| Terminal | Worktree | What it works on |
|---|---|---|
| 1 | `fis-lead-gen` (backend) | Everything under `backend/**`, `scripts/**` |
| 2 | `fis-lead-gen-frontend` | Everything under `frontend/**` |
| 3 | `fis-lead-gen-ops` | Operational / data / verification work that doesn't fit a feature PR (gcloud, pipeline runs, backfills, smoke tests, read-only DB queries) |

Each worktree has its own checked-out branch and its own `.git` state, but they share the same remote (`abedubas-alchemydev/fis-lead-gen`).

cli03 mostly writes to `reports/` rather than committing code. When it does commit, the change is small and isolated — ops runbooks, audit appendices, occasionally a single-file script under `scripts/`. cli03 should NEVER touch `backend/**` or `frontend/**` source — that's cli01 / cli02 territory and parallel writes there create avoidable merge conflicts.

## The three feature types

| Type | Definition | Pattern | Productivity |
|---|---|---|---|
| **BE-only** | No frontend change (new pipeline, admin endpoint, migration, scoring tweak) | Single prompt → cc-cli-01.txt | Terminal 2 runs a different feature in parallel |
| **FE-only** | No backend change (UI polish, new page over existing endpoints, re-styling) | Single prompt → cc-cli-02.txt | Terminal 1 runs a different feature in parallel |
| **Coupled** | Needs new API endpoint + UI to consume it | Two prompts → cc-cli-01.txt + cc-cli-02.txt | Both terminals run the same feature in parallel, merge gate enforces order |
| **Ops** | gcloud / scripts / DB / smoke verification, no FE or BE source change | Single prompt → cc-cli-03.txt | Runs alongside one or both feature CLIs without contention |

## The four templates

| Template | Goes into | When |
|---|---|---|
| `backend-solo.template.txt` | `cc-cli-01.txt` | BE-only feature running alone or alongside an unrelated FE-only feature |
| `frontend-solo.template.txt` | `cc-cli-02.txt` | FE-only feature running alone or alongside an unrelated BE-only feature |
| `backend-coupled.template.txt` | `cc-cli-01.txt` | Coupled feature — backend half |
| `frontend-coupled.template.txt` | `cc-cli-02.txt` | Coupled feature — frontend half (has the polling gate) |

## How coupling is enforced (without idle time)

For coupled features, the frontend template does **everything except the final merge** in parallel with the backend:

1. Both terminals start at **the same moment**.
2. Frontend branches off `develop`, implements against the API contract in `plans/<slug>-YYYY-MM-DD.md`.
3. Frontend opens its PR as a **draft** with body cross-linking the backend PR.
4. Frontend's **Step 7.5** polls `gh pr list --head feature/be-<slug> --state all --json state` every 60 s, waiting for the backend state to flip to `MERGED`.
5. Backend's **Step 10** posts a comment on the frontend PR: "backend merged at <SHA>, cleared to merge." This is the human-readable paper trail; the state check is the machine gate.
6. Frontend marks its PR ready (`gh pr ready`), waits for CI green, merges with `--admin`.

Net effect: wall-clock for a coupled feature ≈ `max(BE time, FE time) + small poll wait`, instead of `BE time + FE time`.

## Placeholders

Templates use `{{DOUBLE_BRACE}}` placeholders. When I generate a concrete cc-cli-01.txt / cc-cli-02.txt from a template, I fill every placeholder. You should never see `{{...}}` in a rendered prompt.

| Placeholder | Meaning | Example |
|---|---|---|
| `{{FEATURE_SLUG}}` | kebab-case, used in branch names | `favorites-and-visits` |
| `{{FEATURE_TITLE}}` | imperative-mood sentence, ≤72 chars, used for commit subject and PR title | `Add per-user favorites and visit tracking for broker-dealers` |
| `{{PLAN_PATH}}` | path to the plan file in `plans/` | `plans/favorites-and-visits-2026-04-24.md` |
| `{{COMMIT_BODY}}` | multi-line commit body in Arvin's voice | (feature-specific) |
| `{{PR_BODY}}` | gh pr create body | (feature-specific) |
| `{{SCOPE_ALLOWED}}` | paths this prompt is allowed to touch | `backend/**, scripts/**` |
| `{{SCOPE_FORBIDDEN}}` | paths this prompt must NOT touch | `frontend/**, fis-placeholder/**` |
| `{{IMPLEMENTATION_STEPS}}` | bulleted Step 4 content | (feature-specific) |
| `{{VERIFY_COMMANDS}}` | bulleted Step 5 content | `pytest app/tests/ -v` etc. |
| `{{STAGED_FILES}}` | files to `git add` in Step 6 | (feature-specific) |

## Queue-based productivity

`plans/queue.md` holds upcoming features, each tagged BE-only / FE-only / coupled. When you're ready to work, I scan the queue and pick:

- **One coupled feature** (both terminals on it, parallel pattern), OR
- **One BE-only + one FE-only** pair of unrelated features (both terminals working in parallel on different things).

Either way, **both terminals are always doing real work**.

## Git / gh expectations baked into every template

- `gh auth switch --user abedubas-alchemydev --hostname github.com` at Step 0.
- Local `develop` is fast-forwarded with `git fetch origin develop:develop` (not checked out in the worktree).
- Branches named `feature/be-<slug>` or `feature/fe-<slug>` — deterministic so the other session can query by head ref.
- All PRs squash-merged with `--admin` (uses branch-protection bypass; allowed because `enforce_admins=false`).
- Zero AI attribution in commits or PRs (eyeballed after every commit via `git log -1 --pretty=full`).
- `--no-verify` and `--amend` are never allowed.
- Files staged by name, never `git add -A`.
- End of workflow: worktree returns to local `develop` (or detached at `develop` if the branch is checked out in the other worktree).

See `CLAUDE.md` at the repo root for the full rule set.
