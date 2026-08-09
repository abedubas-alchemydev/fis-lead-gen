"""Generate the open-source license inventory (doc 14).

Collects the frontend's *production* npm dependency tree (npm ls --omit=dev)
with licenses read from node_modules, and the backend's requirements.txt
packages with license metadata from the PyPI JSON API.

Usage (from the repo root):
    python docs/dox-documentation/export/build_licenses.py
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from datetime import date
from pathlib import Path

EXPORT_DIR = Path(__file__).resolve().parent
DOCS_DIR = EXPORT_DIR.parent
ROOT = DOCS_DIR.parent.parent
FRONTEND = ROOT / "frontend"
BACKEND_REQS = ROOT / "backend" / "requirements.txt"
OUT = DOCS_DIR / "14-open-source-licenses.md"

PERMISSIVE = re.compile(
    r"MIT|ISC|BSD|Apache|Python Software Foundation|PSF|Unlicense|CC0|0BSD|Zlib|WTFPL|BlueOak",
    re.I,
)
WEAK_COPYLEFT = re.compile(r"LGPL|MPL|MOZILLA|EPL|Eclipse|CDDL|Artistic", re.I)
STRONG_COPYLEFT = re.compile(r"(?<!L)\bA?GPL|GNU General Public", re.I)


def classify(license_str: str) -> str:
    s = license_str or ""
    if WEAK_COPYLEFT.search(s):
        return "weak-copyleft"
    if STRONG_COPYLEFT.search(s):
        return "strong-copyleft"
    if PERMISSIVE.search(s):
        return "permissive"
    if not s.strip() or s.strip().upper() in {"UNKNOWN", "UNLICENSED"}:
        return "unknown"
    return "other"


def npm_production_packages() -> dict[str, str]:
    """name -> version for the production dependency tree."""
    proc = subprocess.run(
        "npm ls --omit=dev --all --json",
        cwd=FRONTEND,
        shell=True,
        capture_output=True,
        text=True,
    )
    tree = json.loads(proc.stdout or "{}")
    found: dict[str, str] = {}

    def walk(deps: dict) -> None:
        for name, node in (deps or {}).items():
            version = node.get("version")
            if version and name not in found:
                found[name] = version
            walk(node.get("dependencies") or {})

    walk(tree.get("dependencies") or {})
    return found


def node_license(name: str) -> str:
    pkg = FRONTEND / "node_modules" / name / "package.json"
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    lic = data.get("license") or data.get("licenses") or ""
    if isinstance(lic, dict):
        lic = lic.get("type", "")
    if isinstance(lic, list):
        lic = " OR ".join(
            entry.get("type", "") if isinstance(entry, dict) else str(entry) for entry in lic
        )
    return str(lic).splitlines()[0][:80] if lic else ""


def backend_requirements() -> list[tuple[str, str]]:
    """(name, pinned_version_or_empty) from requirements.txt."""
    out: list[tuple[str, str]] = []
    for raw in BACKEND_REQS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)(?:\[[^\]]+\])?\s*(?:==\s*([^,;\s]+))?", line)
        if m:
            out.append((m.group(1), m.group(2) or ""))
    return out


def pypi_license(name: str, version: str) -> str:
    url = (
        f"https://pypi.org/pypi/{name}/{version}/json"
        if version
        else f"https://pypi.org/pypi/{name}/json"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            info = json.load(resp)["info"]
    except Exception:
        return ""
    lic = (info.get("license_expression") or info.get("license") or "").strip()
    if not lic or len(lic) > 80:
        for c in info.get("classifiers") or []:
            if c.startswith("License ::"):
                lic = c.split("::")[-1].strip()
                break
    return lic.splitlines()[0][:80] if lic else ""


def md_table(rows: list[tuple[str, str, str]]) -> list[str]:
    lines = ["| Package | Version | License |", "|---|---|---|"]
    lines += [f"| `{n}` | {v} | {l or '*not declared*'} |" for n, v, l in rows]
    return lines


def main() -> None:
    npm_pkgs = npm_production_packages()
    npm_rows = sorted(
        (name, ver, node_license(name)) for name, ver in npm_pkgs.items()
    )
    direct_npm = set(
        json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
        .get("dependencies", {})
        .keys()
    )

    py_rows: list[tuple[str, str, str]] = []
    for name, ver in backend_requirements():
        py_rows.append((name, ver or "(unpinned)", pypi_license(name, ver)))
    py_rows.sort()

    all_rows = [("npm", r) for r in npm_rows] + [("pip", r) for r in py_rows]
    buckets: dict[str, list[str]] = {}
    for eco, (name, _v, lic) in all_rows:
        buckets.setdefault(classify(lic), []).append(f"{eco}:{name} ({lic or 'n/a'})")

    today = date.today().isoformat()
    lines = [
        "# 14 — Open-Source License Inventory",
        "",
        "[← Provider Terms References](13-third-party-terms-references.md) | [Index](README.md)",
        "",
        "---",
        "",
        f"Generated **{today}** by `export/build_licenses.py` from the actual dependency",
        "manifests (re-run the script to refresh). Scope: the **production** runtime —",
        "the frontend's `npm ls --omit=dev` tree and the backend's `requirements.txt`.",
        "Development/test-only tooling is excluded. DOX is operated as a hosted service",
        "and is **not distributed** to third parties, which is the usual trigger for most",
        "open-source license obligations; the classification below exists so counsel can",
        "confirm that posture.",
        "",
        "## 14.1 Classification summary",
        "",
        "| Family | Count | Meaning |",
        "|---|---|---|",
        f"| Permissive (MIT/ISC/BSD/Apache/PSF…) | {len(buckets.get('permissive', []))} | "
        "Attribution-style obligations only |",
        f"| Weak copyleft (LGPL/MPL/EPL…) | {len(buckets.get('weak-copyleft', []))} | "
        "File/library-level share-alike; generally safe for unmodified use in a hosted service |",
        f"| Strong copyleft (GPL/AGPL) | {len(buckets.get('strong-copyleft', []))} | "
        "Share-alike; AGPL additionally triggers on network use — review any entry here |",
        f"| Other | {len(buckets.get('other', []))} | Uncommon license strings — listed below |",
        f"| Not declared | {len(buckets.get('unknown', []))} | "
        "No machine-readable license in the package metadata |",
        "",
    ]

    flagged = buckets.get("strong-copyleft", []) + buckets.get("weak-copyleft", [])
    lines += ["### Copyleft entries (review list)", ""]
    if flagged:
        lines += [f"- {item}" for item in sorted(flagged)]
    else:
        lines += ["*None found in the production tree.*"]
    other = buckets.get("other", []) + buckets.get("unknown", [])
    lines += ["", "### Other / undeclared entries", ""]
    if other:
        lines += [f"- {item}" for item in sorted(other)]
    else:
        lines += ["*None.*"]

    lines += [
        "",
        f"## 14.2 Backend Python packages ({len(py_rows)}) — `backend/requirements.txt`",
        "",
        *md_table(py_rows),
        "",
        f"## 14.3 Frontend direct dependencies ({len([r for r in npm_rows if r[0] in direct_npm])})"
        " — `frontend/package.json`",
        "",
        *md_table([r for r in npm_rows if r[0] in direct_npm]),
        "",
        f"## 14.4 Frontend full production tree ({len(npm_rows)} packages, including transitive)",
        "",
        "<details><summary>Expand full inventory</summary>",
        "",
        *md_table(npm_rows),
        "",
        "</details>",
        "",
        "---",
        "",
        "[← Provider Terms References](13-third-party-terms-references.md) | [Index](README.md)",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"wrote {OUT} — npm prod: {len(npm_rows)} (direct {len(direct_npm)}), "
        f"pip: {len(py_rows)}, copyleft flagged: {len(flagged)}"
    )


if __name__ == "__main__":
    main()
