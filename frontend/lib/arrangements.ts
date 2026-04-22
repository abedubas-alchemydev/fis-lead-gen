/**
 * Parse FINRA BrokerCheck arrangement blobs into structured fields.
 *
 * Upstream PDF parser stores Introducing + Industry arrangements as a single
 * labeled string like:
 *   "Name: FOO BAR LLC CRD #: 123 Business Address: 1 MAIN ST Effective Date:
 *    01/02/2024 Description: ACME PROVIDES CUSTODY Firm Operations"
 * Sometimes with an intro sentence before "Name:" and sometimes with the next
 * section's header ("Firm Operations", "Clearing Arrangements", etc.) bleeding
 * onto the tail. This module splits the blob so the UI can render labeled
 * rows instead of a raw paragraph.
 */

export interface ParsedArrangement {
  /** Text before the first "Name:" label — usually the arrangement's summary. */
  intro: string | null;
  partnerName: string | null;
  partnerCrd: string | null;
  partnerAddress: string | null;
  /** Raw date string as extracted (e.g. "08/14/2023"). Caller formats. */
  effectiveDate: string | null;
  /** The "Description:" value — narrative about what the partner does. */
  description: string | null;
}

const EMPTY: ParsedArrangement = {
  intro: null,
  partnerName: null,
  partnerCrd: null,
  partnerAddress: null,
  effectiveDate: null,
  description: null,
};

const TRAILING_SECTION_HEADERS = [
  "Firm Operations",
  "Clearing Arrangements",
  "Industry Arrangements",
  "Introducing Arrangements",
  "Control Persons",
  "Direct Owners",
  "Executive Officers",
  "Types of Business",
];

function stripTrailingSectionHeader(text: string): string {
  let result = text.trim();
  let changed = true;
  while (changed) {
    changed = false;
    for (const header of TRAILING_SECTION_HEADERS) {
      if (result.endsWith(header)) {
        result = result.slice(0, -header.length).trim();
        changed = true;
      }
    }
  }
  return result;
}

type FieldKey = Exclude<keyof ParsedArrangement, "intro">;

function labelToFieldKey(label: string): FieldKey | null {
  const normalized = label.replace(/\s+/g, " ").trim();
  switch (normalized) {
    case "Name:":
      return "partnerName";
    case "CRD #:":
    case "CRD#:":
      return "partnerCrd";
    case "Business Address:":
      return "partnerAddress";
    case "Effective Date:":
      return "effectiveDate";
    case "Description:":
      return "description";
    default:
      return null;
  }
}

const LABEL_REGEX = /(Name:|CRD\s*#:|Business Address:|Effective Date:|Description:)/g;

export function parseArrangementBlob(
  raw: string | null | undefined,
): ParsedArrangement {
  if (!raw || !raw.trim()) return { ...EMPTY };

  const text = stripTrailingSectionHeader(raw);
  if (!text) return { ...EMPTY };

  const labels: Array<{ key: FieldKey; start: number; end: number }> = [];
  LABEL_REGEX.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = LABEL_REGEX.exec(text)) !== null) {
    const key = labelToFieldKey(match[1]);
    if (key) {
      labels.push({ key, start: match.index, end: match.index + match[0].length });
    }
  }

  if (labels.length === 0) {
    return { ...EMPTY, intro: text };
  }

  const out: ParsedArrangement = { ...EMPTY };
  const introText = text.slice(0, labels[0].start).trim();
  if (introText) out.intro = introText;

  labels.forEach((lab, i) => {
    const nextStart = i + 1 < labels.length ? labels[i + 1].start : text.length;
    const value = text.slice(lab.end, nextStart).trim();
    if (value) out[lab.key] = value;
  });

  return out;
}

export function hasStructuredFields(parsed: ParsedArrangement): boolean {
  return Boolean(
    parsed.partnerName ||
      parsed.partnerCrd ||
      parsed.partnerAddress ||
      parsed.effectiveDate ||
      parsed.description,
  );
}
