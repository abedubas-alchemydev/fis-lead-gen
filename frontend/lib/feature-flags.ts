// Frontend feature kill-switches.
//
// Per product direction the app exposes **no data export or extraction**
// surfaces. These flags hide the relevant UI (nav entries, routes, and the
// per-firm "Find emails" sections) while leaving all backend code intact —
// flip a flag back to `true` to fully restore the feature, no other change
// needed. Typed as `boolean` (not the literal `false`) so toggling them
// doesn't trip "condition is always false" lint at every call site.

export const EMAIL_EXTRACTION_ENABLED: boolean = false;
export const DATA_EXPORT_ENABLED: boolean = false;
