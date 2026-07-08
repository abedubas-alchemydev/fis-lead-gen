import { apiRequest, buildApiPath } from "@/lib/api";
import type { SavedContact } from "@/types/saved-contact";

// Thin wrappers over the BE Save Contact endpoints, following the same
// apiRequest convention as lib/email-extractor.ts + lib/api.ts. The list
// endpoint is per-user (the BE scopes to the session user); POST is
// idempotent-friendly on the BE and returns the persisted row (whose `id` is
// the saved-contact id used to un-save), DELETE 204s to undefined.

// GET /api/v1/saved-contacts — optionally filtered to one source
// (e.g. "discovered_email") so a caller can hydrate just the saved-state map
// it cares about instead of every source.
export async function listSavedContacts(
  source?: string
): Promise<SavedContact[]> {
  return apiRequest<SavedContact[]>(
    buildApiPath("/api/v1/saved-contacts", { source })
  );
}

// POST /api/v1/saved-contacts — saves (source, contact_id) and returns the
// persisted SavedContact. The returned `id` is the saved-contact id the
// caller stores to later un-save via deleteSavedContact.
export async function saveContact(input: {
  source: string;
  contact_id: number;
}): Promise<SavedContact> {
  return apiRequest<SavedContact>("/api/v1/saved-contacts", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// DELETE /api/v1/saved-contacts/{savedId} — un-saves by the saved-contact id
// (not the source contact_id). 204 → undefined.
export async function deleteSavedContact(savedId: number): Promise<void> {
  await apiRequest<void>(`/api/v1/saved-contacts/${savedId}`, {
    method: "DELETE",
  });
}
