// Maps backend sub-pipeline names (as written into PipelineRun.notes.ran)
// to short, human-readable labels for the detail-page loading screen.
// Both the BD detail client (master-list/{id}) and IA detail client
// (advisor-list/{id}) consume this so the "Refreshing X, Y…" copy stays
// honest about what's running without leaking internal names like
// "investment_advisor_refresh_owners_officers" into the UI.
//
// Keep in sync with the SUB_LABEL maps on the backend:
//   backend/app/services/refresh_all_orchestrator.py (BD)
//   backend/app/services/advisor_refresh_orchestrator.py (IA)
const PIPELINE_LABELS: Record<string, string> = {
  // ── Broker-dealer sub-pipelines ─────────────────────────────────
  financial_pdf_pipeline_single: "financials",
  broker_dealer_resolve_website: "website",
  broker_dealer_health_check: "firm details",
  broker_dealer_enrich_contacts: "contacts",
  broker_dealer_refresh_filings: "filings",
  broker_dealer_refresh_clearing: "clearing",
  // ── Investment-advisor sub-pipelines ───────────────────────────
  investment_advisor_refresh_owners_officers: "owners",
  investment_advisor_resolve_website: "website",
  investment_advisor_refresh_filings: "filings",
  investment_advisor_enrich_contacts: "contacts",
  investment_advisor_refresh_iapd_summary: "iapd",
};

/**
 * Convert a raw pipeline name from PipelineRun.notes.ran into a short
 * human-readable label. Unknown names fall back to a heuristic — strip
 * the ``broker_dealer_`` / ``investment_advisor_`` prefix and convert
 * underscores to spaces — so the UI degrades gracefully when the
 * backend adds a new sub-pipeline before the FE catches up.
 */
export function labelForPipeline(name: string): string {
  const known = PIPELINE_LABELS[name];
  if (known) return known;
  return name
    .replace(/^broker_dealer_/, "")
    .replace(/^investment_advisor_/, "")
    .replace(/_/g, " ");
}

/**
 * Render a comma-joined friendly list of sub-pipelines for the loading
 * screen label, deduplicated to handle the rare case where the backend
 * writes the same name twice (e.g. on a manual replay).
 */
export function joinPipelineLabels(names: readonly string[]): string {
  const seen = new Set<string>();
  const labels: string[] = [];
  for (const raw of names) {
    const label = labelForPipeline(raw);
    if (!seen.has(label)) {
      seen.add(label);
      labels.push(label);
    }
  }
  return labels.join(", ");
}
