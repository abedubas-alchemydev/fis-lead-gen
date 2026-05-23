# Clearing-agency / SRO membership directories

Seed data for `scripts/import_clearing_agency_memberships.py`. Each CSV is the
member/participant list for one clearing agency. The importer matches the listed
firm names to our broker-dealer / investment-advisor records and writes
`clearing_agency_memberships` rows.

## Why files (not a scraper)

The OCC member directory and DTCC's DTC/NSCC/FICC directories block automated
fetches (HTTP 403) and publish their lists as gated, varied-format exports. So
an operator downloads the official exports, normalizes them to the CSV shape
below, and drops them here. Refresh = replace the file and re-run the importer.

> The committed files are **small illustrative starter samples** with blank
> member numbers. Replace them with the full official exports before the labels
> are treated as authoritative. Provenance (`source_file`, `source_version` =
> content hash) is recorded on every membership row.

## Files → agency

| File                   | Agency code | Source |
|------------------------|-------------|--------|
| `occ_members.csv`      | `OCC`       | theocc.com/company-information/member-directory |
| `dtc_participants.csv` | `DTC`       | dtcc.com DTC Member Directories |
| `nscc_members.csv`     | `NSCC`      | dtcc.com NSCC Directories |
| `ficc_gov_members.csv` | `FICC-GOV`  | dtcc.com FICC-GOV Directories |
| `ficc_mbs_members.csv` | `FICC-MBS`  | dtcc.com FICC-MBS Directories |

## Column contract

Header row required. Columns (case-insensitive):

| Column          | Required | Notes |
|-----------------|----------|-------|
| `member_name`   | yes      | Firm name as listed in the directory (the match key). |
| `member_number` | no       | OCC member # / DTCC participant #. Stored as provenance. |
| `agency`        | no       | Inferred from the filename if absent; if present, must be one of the codes above. |
| `city`          | no       | Ignored today; kept for future disambiguation. |
| `state`         | no       | Ignored today. |

A full run requires all five files (so firms absent from every list can be
labeled "not a member" rather than "unknown"). Use `--agency OCC,DTC` for
incremental updates.
