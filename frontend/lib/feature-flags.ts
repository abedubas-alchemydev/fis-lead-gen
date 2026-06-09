// Frontend feature kill-switches.
//
// These flags gate the data export / extraction surfaces: the Email Extractor
// nav entry + /email-extractor route, the /export route, and the per-firm
// "Find emails" (Discovered Emails) sections on the broker-dealer and advisor
// detail pages. They are currently **enabled** — the per-firm email discovery
// was restored at client request. Flip a flag back to `false` to hide the
// relevant UI again while leaving all backend code intact, no other change
// needed. Typed as `boolean` (not a literal) so toggling them doesn't trip
// "condition is always true/false" lint at every call site.

export const EMAIL_EXTRACTION_ENABLED: boolean = true;
export const DATA_EXPORT_ENABLED: boolean = true;
