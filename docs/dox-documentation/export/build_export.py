"""Build the single-file exports of the DOX documentation set.

Concatenates the numbered documents into one Markdown file and renders a
self-contained HTML file (print-friendly; opens in any browser or Word).

Usage (from the repo root):
    python docs/dox-documentation/export/build_export.py

Requires: pip install markdown
"""

from __future__ import annotations

import base64
import re
from datetime import date
from pathlib import Path

import markdown

DOCS_DIR = Path(__file__).resolve().parent.parent
EXPORT_DIR = Path(__file__).resolve().parent

FILES = [
    "README.md",
    "01-product-overview.md",
    "02-system-architecture.md",
    "03-data-sources-and-provenance.md",
    "04-third-party-services.md",
    "05-personal-data-and-privacy.md",
    "06-outreach-and-email-compliance.md",
    "07-ai-features-and-llm-data-flows.md",
    "08-security.md",
    "09-user-guide.md",
    "10-operations-and-environments.md",
    "11-legal-considerations-for-counsel.md",
    "12-glossary.md",
    "13-third-party-terms-references.md",
    "14-open-source-licenses.md",
]

# Navigation footers/headers like "[← Index](README.md) | [Next: ...](...)"
NAV_LINE = re.compile(r"^\[(?:←|Index).*\]\(.*\.md\).*$|^\[.*\]\(README\.md\).*\|.*$")
MD_LINK = re.compile(r"\((?:\./)?((?:\d{2}-[a-z0-9-]+|README))\.md(#[a-z0-9-]+)?\)")

CSS = """
body { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; color: #1a202c;
       max-width: 54em; margin: 0 auto; padding: 2em 2.5em; line-height: 1.55; }
h1 { font-size: 1.7em; border-bottom: 3px solid #1a365d; padding-bottom: .3em; color: #1a365d;
     margin-top: 2.2em; }
h1.cover { font-size: 2.3em; border: none; margin-top: 1em; }
h2 { font-size: 1.25em; color: #2c5282; margin-top: 1.8em; }
h3 { font-size: 1.05em; color: #2d3748; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .92em; }
th, td { border: 1px solid #cbd5e0; padding: .45em .6em; text-align: left; vertical-align: top; }
th { background: #edf2f7; }
code { background: #edf2f7; padding: .1em .3em; border-radius: 3px; font-size: .9em; }
pre { background: #f7fafc; border: 1px solid #e2e8f0; padding: .8em; overflow-x: auto;
      font-size: .85em; line-height: 1.35; }
pre code { background: none; padding: 0; }
blockquote { border-left: 4px solid #2c5282; margin-left: 0; padding: .2em 1em;
             background: #ebf4ff; color: #2a4365; }
a { color: #2b6cb0; }
img { max-width: 100%; border: 1px solid #cbd5e0; border-radius: 4px; margin: .5em 0; }
hr { border: none; border-top: 1px solid #e2e8f0; margin: 2em 0; }
.cover-meta { color: #4a5568; font-size: 1.05em; }
@media print {
  body { max-width: none; padding: 0; font-size: 10.5pt; }
  h1 { page-break-before: always; }
  h1.cover, h1.toc-title { page-break-before: avoid; }
  table, pre, blockquote { page-break-inside: avoid; }
  a { color: inherit; text-decoration: none; }
}
"""


def section_anchor(name: str) -> str:
    return name.lower().replace(".md", "")


def load_and_clean(name: str) -> str:
    text = (DOCS_DIR / name).read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not NAV_LINE.match(ln.strip())]
    cleaned = "\n".join(lines)
    # Point cross-document links at in-page anchors.
    cleaned = MD_LINK.sub(lambda m: f"(#{section_anchor(m.group(1))})", cleaned)
    # Drop the trailing separator the nav used to sit under.
    return cleaned.strip().rstrip("-").rstrip()


def build_combined_markdown() -> str:
    today = date.today().isoformat()
    parts = [
        "# DOX — Complete Documentation (Combined Edition)\n",
        f"*Generated {today} from `docs/dox-documentation/` — the per-topic files in that "
        "directory are the maintained source of truth.*\n",
        "\n## Contents\n",
    ]
    for name in FILES:
        if name == "README.md":
            title = "Introduction & Index"
        else:
            words = name[3:-3].replace("-", " ").title()
            for raw, fixed in (("Ai ", "AI "), ("Llm", "LLM"), (" And ", " and "), (" For ", " for ")):
                words = words.replace(raw, fixed)
            title = f"{name[:2]} — {words}"
        parts.append(f"- [{title}](#{section_anchor(name)})")
    parts.append("\n---\n")
    for name in FILES:
        parts.append(f'\n<a id="{section_anchor(name)}"></a>\n')
        parts.append(load_and_clean(name))
        parts.append("\n\n---\n")
    return "\n".join(parts)


def inline_images(html: str) -> str:
    """Replace relative image srcs with base64 data URIs (self-contained HTML)."""

    def repl(m: re.Match) -> str:
        img = DOCS_DIR / "images" / m.group(1)
        if not img.is_file():
            return m.group(0)
        encoded = base64.b64encode(img.read_bytes()).decode("ascii")
        return f'src="data:image/png;base64,{encoded}"'

    return re.sub(r'src="images/([^"]+)"', repl, html)


def main() -> None:
    combined_md = build_combined_markdown()
    md_path = EXPORT_DIR / "DOX-Complete-Documentation.md"
    # The combined .md sits in export/, one level below the images directory.
    md_path.write_text(
        combined_md.replace("](images/", "](../images/"), encoding="utf-8"
    )

    body = markdown.markdown(
        combined_md, extensions=["tables", "fenced_code", "sane_lists"]
    )
    # Promote the document title styling.
    body = body.replace(
        "<h1>DOX — Complete Documentation (Combined Edition)</h1>",
        '<h1 class="cover">DOX — Complete Documentation</h1>'
        '<p class="cover-meta">Combined edition for review · generated '
        f"{date.today().isoformat()}</p>",
        1,
    )
    def page(content: str) -> str:
        return (
            "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<title>DOX — Complete Documentation</title>\n"
            f"<style>{CSS}</style>\n</head>\n<body>\n{content}\n</body>\n</html>\n"
        )

    html_path = EXPORT_DIR / "DOX-Complete-Documentation.html"
    html_path.write_text(page(inline_images(body)), encoding="utf-8")

    print(f"wrote {md_path} ({len(combined_md):,} chars)")
    print(f"wrote {html_path} ({html_path.stat().st_size:,} bytes, images inlined)")
    print(
        "DOCX: run from this directory —\n"
        "  pandoc DOX-Complete-Documentation.md -f gfm -t docx "
        "-o DOX-Complete-Documentation.docx"
    )


if __name__ == "__main__":
    main()
