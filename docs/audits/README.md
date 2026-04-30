# Audits

Permanent record of audits run against the fis-lead-gen codebase.
Time-sensitive ops notes (incident postmortems, deploy logs, smoke
results) live in `reports/` (gitignored) instead.

## Convention

- **File name:** `<scope>-<topic>-YYYY-MM-DD.md`
  - scope: `be` / `fe` / `infra` / `cross`
  - topic: short kebab-case slug (e.g. `test-coverage`,
    `responsive`, `drift`)
- **Frontmatter / opening:** trigger context (what prompted the
  audit), method (what was scanned), date, author CLI if applicable.
- **Findings:** prioritized HIGH / MEDIUM / LOW with file:line
  citations.
- **Recommendations:** prioritized follow-up tasks, each ideally
  scoped to one PR.

## Index

| Date | Audit | Highlights |
|---|---|---|
| 2026-04-29 | [Drift audit](../../reports/drift-audit-2026-04-29.md) — local-only | 22 tables, 0 critical drift, 1 LOW doc-rot |
| 2026-04-30 | [BE test coverage](be-test-coverage-2026-04-30.md) | 65.2% overall, 23 HIGH gaps; CI integration-skip gap surfaced |
| 2026-04-30 | [FE responsive](fe-responsive-2026-04-30.md) | 3 HIGH (table overflow), 4 MEDIUM, 1 LOW |

## When to add a new audit

Run an audit (and create a docs/audits/ entry) when:
- You suspect a class of issues lurks across the codebase
  (drift, missing tests, accessibility, performance)
- A post-incident analysis identifies a structural pattern
  worth verifying everywhere
- Pre-release: as a safety pass before a major refactor

## When NOT to add a new audit
- One-off bug investigations -> use `reports/incident-...` instead
- Pre-deploy smoke checks -> use `scripts/ops/smoke-prod.ps1`
- Per-PR test coverage -> use the existing pytest --cov flag
