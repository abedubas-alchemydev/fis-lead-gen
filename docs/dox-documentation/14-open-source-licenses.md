# 14 — Open-Source License Inventory

[← Provider Terms References](13-third-party-terms-references.md) | [Index](README.md)

---

Generated **2026-06-12** by `export/build_licenses.py` from the actual dependency
manifests (re-run the script to refresh). Scope: the **production** runtime —
the frontend's `npm ls --omit=dev` tree and the backend's `requirements.txt`.
Development/test-only tooling is excluded. DOX is operated as a hosted service
and is **not distributed** to third parties, which is the usual trigger for most
open-source license obligations; the classification below exists so counsel can
confirm that posture.

## 14.1 Classification summary

| Family | Count | Meaning |
|---|---|---|
| Permissive (MIT/ISC/BSD/Apache/PSF…) | 170 | Attribution-style obligations only |
| Weak copyleft (LGPL/MPL/EPL…) | 3 | File/library-level share-alike; generally safe for unmodified use in a hosted service |
| Strong copyleft (GPL/AGPL) | 0 | Share-alike; AGPL additionally triggers on network use — review any entry here |
| Other | 1 | Uncommon license strings — listed below |
| Not declared | 3 | No machine-readable license in the package metadata |

### Copyleft entries (review list)

- npm:@img/sharp-win32-x64 (Apache-2.0 AND LGPL-3.0-or-later)
- pip:psycopg (LGPL-3.0-only)
- pip:py3-validate-email (LGPL)

### Other / undeclared entries

- npm:@better-auth/drizzle-adapter (n/a)
- npm:@better-fetch/fetch (n/a)
- npm:caniuse-lite (CC-BY-4.0)
- pip:pgvector (n/a)

## 14.2 Backend Python packages (28) — `backend/requirements.txt`

| Package | Version | License |
|---|---|---|
| `SQLAlchemy` | 2.0.45 | MIT |
| `aiosmtplib` | (unpinned) | MIT |
| `alembic` | 1.14.1 | MIT |
| `email-validator` | 2.2.0 | Unlicense |
| `fastapi` | (unpinned) | MIT |
| `google-auth` | (unpinned) | Apache 2.0 |
| `google-cloud-run` | (unpinned) | Apache 2.0 |
| `google-cloud-storage` | (unpinned) | Apache-2.0 |
| `google-cloud-vision` | 3.7.4 | Apache 2.0 |
| `greenlet` | 3.2.4 | MIT AND Python-2.0 |
| `httpx` | 0.28.1 | BSD-3-Clause |
| `openpyxl` | (unpinned) | MIT |
| `pdfplumber` | (unpinned) | MIT License |
| `pgvector` | (unpinned) | *not declared* |
| `psycopg` | 3.2.13 | LGPL-3.0-only |
| `py3-validate-email` | 1.0.5.post1 | LGPL |
| `pydantic-settings` | 2.7.0 | MIT |
| `pypdf` | (unpinned) | BSD-3-Clause |
| `pypdfium2` | (unpinned) | BSD-3-Clause, Apache-2.0, dependency licenses |
| `python-docx` | (unpinned) | MIT |
| `python-dotenv` | 1.0.1 | BSD-3-Clause |
| `python-multipart` | (unpinned) | Apache-2.0 |
| `python-pptx` | (unpinned) | MIT |
| `rapidfuzz` | (unpinned) | MIT |
| `requests` | (unpinned) | Apache-2.0 |
| `selectolax` | (unpinned) | MIT |
| `striprtf` | (unpinned) | BSD-3-Clause |
| `uvicorn` | (unpinned) | BSD-3-Clause |

## 14.3 Frontend direct dependencies (10) — `frontend/package.json`

| Package | Version | License |
|---|---|---|
| `better-auth` | 1.6.2 | MIT |
| `clsx` | 2.1.1 | MIT |
| `lucide-react` | 0.471.2 | ISC |
| `next` | 15.5.18 | MIT |
| `nodemailer` | 8.0.8 | MIT-0 |
| `pg` | 8.20.0 | MIT |
| `react` | 18.3.1 | MIT |
| `react-dom` | 18.3.1 | MIT |
| `react-markdown` | 9.1.0 | MIT |
| `rehype-sanitize` | 6.0.0 | MIT |

## 14.4 Frontend full production tree (149 packages, including transitive)

<details><summary>Expand full inventory</summary>

| Package | Version | License |
|---|---|---|
| `@better-auth/core` | 1.6.2 | MIT |
| `@better-auth/drizzle-adapter` | 1.6.2 | *not declared* |
| `@better-auth/kysely-adapter` | 1.6.2 | MIT |
| `@better-auth/memory-adapter` | 1.6.2 | MIT |
| `@better-auth/mongo-adapter` | 1.6.2 | MIT |
| `@better-auth/prisma-adapter` | 1.6.2 | MIT |
| `@better-auth/telemetry` | 1.6.2 | MIT |
| `@better-auth/utils` | 0.4.0 | MIT |
| `@better-fetch/fetch` | 1.1.21 | *not declared* |
| `@emnapi/runtime` | 1.10.0 | MIT |
| `@img/colour` | 1.1.0 | MIT |
| `@img/sharp-win32-x64` | 0.34.5 | Apache-2.0 AND LGPL-3.0-or-later |
| `@next/env` | 15.5.18 | MIT |
| `@next/swc-win32-x64-msvc` | 15.5.18 | MIT |
| `@noble/ciphers` | 2.1.1 | MIT |
| `@noble/hashes` | 2.0.1 | MIT |
| `@opentelemetry/api` | 1.9.1 | Apache-2.0 |
| `@opentelemetry/semantic-conventions` | 1.40.0 | Apache-2.0 |
| `@standard-schema/spec` | 1.1.0 | MIT |
| `@swc/helpers` | 0.5.15 | Apache-2.0 |
| `@types/debug` | 4.1.13 | MIT |
| `@types/estree` | 1.0.9 | MIT |
| `@types/estree-jsx` | 1.0.5 | MIT |
| `@types/hast` | 3.0.4 | MIT |
| `@types/mdast` | 4.0.4 | MIT |
| `@types/ms` | 2.1.0 | MIT |
| `@types/prop-types` | 15.7.15 | MIT |
| `@types/react` | 18.3.28 | MIT |
| `@types/unist` | 3.0.3 | MIT |
| `@ungap/structured-clone` | 1.3.0 | ISC |
| `bail` | 2.0.2 | MIT |
| `better-auth` | 1.6.2 | MIT |
| `better-call` | 1.3.5 | MIT |
| `caniuse-lite` | 1.0.30001787 | CC-BY-4.0 |
| `ccount` | 2.0.1 | MIT |
| `character-entities` | 2.0.2 | MIT |
| `character-entities-html4` | 2.1.0 | MIT |
| `character-entities-legacy` | 3.0.0 | MIT |
| `character-reference-invalid` | 2.0.1 | MIT |
| `client-only` | 0.0.1 | MIT |
| `clsx` | 2.1.1 | MIT |
| `comma-separated-tokens` | 2.0.3 | MIT |
| `csstype` | 3.2.3 | MIT |
| `debug` | 4.4.3 | MIT |
| `decode-named-character-reference` | 1.3.0 | MIT |
| `defu` | 6.1.7 | MIT |
| `dequal` | 2.0.3 | MIT |
| `detect-libc` | 2.1.2 | Apache-2.0 |
| `devlop` | 1.1.0 | MIT |
| `estree-util-is-identifier-name` | 3.0.0 | MIT |
| `extend` | 3.0.2 | MIT |
| `hast-util-sanitize` | 5.0.2 | MIT |
| `hast-util-to-jsx-runtime` | 2.3.6 | MIT |
| `hast-util-whitespace` | 3.0.0 | MIT |
| `html-url-attributes` | 3.0.1 | MIT |
| `inline-style-parser` | 0.2.7 | MIT |
| `is-alphabetical` | 2.0.1 | MIT |
| `is-alphanumerical` | 2.0.1 | MIT |
| `is-decimal` | 2.0.1 | MIT |
| `is-hexadecimal` | 2.0.1 | MIT |
| `is-plain-obj` | 4.1.0 | MIT |
| `jose` | 6.2.2 | MIT |
| `js-tokens` | 4.0.0 | MIT |
| `kysely` | 0.28.17 | MIT |
| `longest-streak` | 3.1.0 | MIT |
| `loose-envify` | 1.4.0 | MIT |
| `lucide-react` | 0.471.2 | ISC |
| `mdast-util-from-markdown` | 2.0.3 | MIT |
| `mdast-util-mdx-expression` | 2.0.1 | MIT |
| `mdast-util-mdx-jsx` | 3.2.0 | MIT |
| `mdast-util-mdxjs-esm` | 2.0.1 | MIT |
| `mdast-util-phrasing` | 4.1.0 | MIT |
| `mdast-util-to-hast` | 13.2.1 | MIT |
| `mdast-util-to-markdown` | 2.1.2 | MIT |
| `mdast-util-to-string` | 4.0.0 | MIT |
| `micromark` | 4.0.2 | MIT |
| `micromark-core-commonmark` | 2.0.3 | MIT |
| `micromark-factory-destination` | 2.0.1 | MIT |
| `micromark-factory-label` | 2.0.1 | MIT |
| `micromark-factory-space` | 2.0.1 | MIT |
| `micromark-factory-title` | 2.0.1 | MIT |
| `micromark-factory-whitespace` | 2.0.1 | MIT |
| `micromark-util-character` | 2.1.1 | MIT |
| `micromark-util-chunked` | 2.0.1 | MIT |
| `micromark-util-classify-character` | 2.0.1 | MIT |
| `micromark-util-combine-extensions` | 2.0.1 | MIT |
| `micromark-util-decode-numeric-character-reference` | 2.0.2 | MIT |
| `micromark-util-decode-string` | 2.0.1 | MIT |
| `micromark-util-encode` | 2.0.1 | MIT |
| `micromark-util-html-tag-name` | 2.0.1 | MIT |
| `micromark-util-normalize-identifier` | 2.0.1 | MIT |
| `micromark-util-resolve-all` | 2.0.1 | MIT |
| `micromark-util-sanitize-uri` | 2.0.1 | MIT |
| `micromark-util-subtokenize` | 2.1.0 | MIT |
| `micromark-util-symbol` | 2.0.1 | MIT |
| `micromark-util-types` | 2.0.2 | MIT |
| `ms` | 2.1.3 | MIT |
| `nanoid` | 3.3.12 | MIT |
| `nanostores` | 1.2.0 | MIT |
| `next` | 15.5.18 | MIT |
| `nodemailer` | 8.0.8 | MIT-0 |
| `parse-entities` | 4.0.2 | MIT |
| `pg` | 8.20.0 | MIT |
| `pg-cloudflare` | 1.3.0 | MIT |
| `pg-connection-string` | 2.12.0 | MIT |
| `pg-int8` | 1.0.1 | ISC |
| `pg-pool` | 3.13.0 | MIT |
| `pg-protocol` | 1.13.0 | MIT |
| `pg-types` | 2.2.0 | MIT |
| `pgpass` | 1.0.5 | MIT |
| `picocolors` | 1.1.1 | ISC |
| `postcss` | 8.4.31 | MIT |
| `postgres-array` | 2.0.0 | MIT |
| `postgres-bytea` | 1.0.1 | MIT |
| `postgres-date` | 1.0.7 | MIT |
| `postgres-interval` | 1.2.0 | MIT |
| `property-information` | 7.1.0 | MIT |
| `react` | 18.3.1 | MIT |
| `react-dom` | 18.3.1 | MIT |
| `react-markdown` | 9.1.0 | MIT |
| `rehype-sanitize` | 6.0.0 | MIT |
| `remark-parse` | 11.0.0 | MIT |
| `remark-rehype` | 11.1.2 | MIT |
| `rou3` | 0.7.12 | MIT |
| `scheduler` | 0.23.2 | MIT |
| `semver` | 7.7.4 | ISC |
| `set-cookie-parser` | 3.1.0 | MIT |
| `sharp` | 0.34.5 | Apache-2.0 |
| `source-map-js` | 1.2.1 | BSD-3-Clause |
| `space-separated-tokens` | 2.0.2 | MIT |
| `split2` | 4.2.0 | ISC |
| `stringify-entities` | 4.0.4 | MIT |
| `style-to-js` | 1.1.21 | MIT |
| `style-to-object` | 1.0.14 | MIT |
| `styled-jsx` | 5.1.6 | MIT |
| `trim-lines` | 3.0.1 | MIT |
| `trough` | 2.2.0 | MIT |
| `tslib` | 2.8.1 | 0BSD |
| `unified` | 11.0.5 | MIT |
| `unist-util-is` | 6.0.1 | MIT |
| `unist-util-position` | 5.0.0 | MIT |
| `unist-util-stringify-position` | 4.0.0 | MIT |
| `unist-util-visit` | 5.1.0 | MIT |
| `unist-util-visit-parents` | 6.0.2 | MIT |
| `vfile` | 6.0.3 | MIT |
| `vfile-message` | 4.0.3 | MIT |
| `xtend` | 4.0.2 | MIT |
| `zod` | 4.3.6 | MIT |
| `zwitch` | 2.0.4 | MIT |

</details>

---

[← Provider Terms References](13-third-party-terms-references.md) | [Index](README.md)
