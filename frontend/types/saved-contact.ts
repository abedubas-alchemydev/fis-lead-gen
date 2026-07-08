// Typed contract for the Save Contact endpoints. Mirrors the BE
// `SavedContact` response schema (being built to match). A saved contact is
// a polymorphic pointer: `source` names the origin table (e.g.
// "discovered_email") and `contact_id` is that table's row id. The denormalized
// name/title/email/… fields are snapshotted by the BE at save time so the
// Saved Contacts list renders without re-joining the source table.
export interface SavedContact {
  id: number;
  source: string;
  contact_id: number;
  name: string | null;
  title: string | null;
  email: string | null;
  company: string | null;
  phone: string | null;
  linkedin_url: string | null;
  created_at: string;
}
