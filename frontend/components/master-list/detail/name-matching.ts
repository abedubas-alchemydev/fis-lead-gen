import type { ExecutiveContactItem } from "@/lib/types";

// Owner / officer name parsing lifted out of broker-dealer-detail-client so
// the page client can stay focused on layout. Behaviour is unchanged from the
// pre-restyle implementation: FINRA records use "Last, First [suffix]" while
// Apollo enrichment uses free-form "First [Middle] Last [Suffix]". We
// normalise both shapes to {first, last} (lowercased, suffix-stripped) so a
// FINRA officer can be matched against an Apollo executive contact.

const NAME_SUFFIXES = new Set(["jr", "sr", "ii", "iii", "iv", "v"]);

function stripSuffix(token: string): string {
  return token.replace(/[^a-z]/gi, "").toLowerCase();
}

function parseFinraName(display: string | null | undefined): { first: string; last: string } | null {
  if (!display) return null;
  const trimmed = display.trim();
  const commaIdx = trimmed.indexOf(",");
  if (commaIdx < 0) return null;
  const last = trimmed.slice(0, commaIdx).trim().toLowerCase();
  const firstTokens = trimmed
    .slice(commaIdx + 1)
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  const first = (firstTokens[0] ?? "").toLowerCase();
  if (!last || !first) return null;
  return { last, first };
}

function parseApolloName(name: string | null | undefined): { first: string; last: string } | null {
  if (!name) return null;
  const tokens = name.trim().split(/\s+/).filter(Boolean);
  if (tokens.length < 2) return null;
  let lastIdx = tokens.length - 1;
  while (lastIdx > 0 && NAME_SUFFIXES.has(stripSuffix(tokens[lastIdx]))) {
    lastIdx -= 1;
  }
  const first = tokens[0].toLowerCase();
  const last = tokens[lastIdx].toLowerCase();
  if (!first || !last || first === last) return null;
  return { first, last };
}

function isOrgLevelContact(contact: ExecutiveContactItem): boolean {
  return contact.title === "Company (Organization Profile)";
}

export function nameMatches(finraDisplay: string, contact: ExecutiveContactItem): boolean {
  if (isOrgLevelContact(contact)) return false;
  const finra = parseFinraName(finraDisplay);
  if (!finra) return false;
  const apollo = parseApolloName(contact.name);
  if (!apollo) return false;
  return finra.last === apollo.last && finra.first === apollo.first;
}

export type OfficerEntity =
  | { type: "person"; first_name: string; last_name: string; title: string }
  | { type: "organization"; org_name: string; title: string };

export function toOfficerEntity(record: { name: string; title: string }): OfficerEntity {
  const parsed = parseFinraName(record.name);
  if (parsed) {
    return {
      type: "person",
      first_name: parsed.first,
      last_name: parsed.last,
      title: record.title,
    };
  }
  return { type: "organization", org_name: record.name, title: record.title };
}

export function dedupOfficers(entities: OfficerEntity[]): OfficerEntity[] {
  const seen = new Set<string>();
  const out: OfficerEntity[] = [];
  for (const entity of entities) {
    const key =
      entity.type === "person"
        ? `p|${entity.first_name}|${entity.last_name}`
        : `o|${entity.org_name.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(entity);
  }
  return out;
}
