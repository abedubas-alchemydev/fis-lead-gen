// Frontend feature kill-switches.
//
// Policy: the app allows **email extraction** but **not data export**.
//   - EMAIL_EXTRACTION_ENABLED gates the Email Extractor nav entry +
//     /email-extractor route and the per-firm "Find emails" (Discovered
//     Emails) sections on the broker-dealer and advisor detail pages.
//     Enabled — the client uses per-firm email discovery on the IA profile.
//   - DATA_EXPORT_ENABLED gates the /export route. Disabled — no data export.
// Backend code is left intact regardless; a flag only toggles the UI surface.
// Typed as `boolean` (not a literal) so toggling doesn't trip "condition is
// always true/false" lint at every call site.

export const EMAIL_EXTRACTION_ENABLED: boolean = true;
export const DATA_EXPORT_ENABLED: boolean = false;
