// Compact dl block for clearing-arrangement metadata (CRD #, address,
// effective date, free-text details). Returns null when every field is empty
// so the parent doesn't render an empty container. Restyled to use the
// project's --text / --text-dim / --text-muted tokens (matching the panel
// content style on /dashboard and /master-list) instead of raw slate-X.
export function ArrangementFields({
  crd,
  address,
  effective,
  details,
}: {
  crd: string | null;
  address: string | null;
  effective: string | null;
  details: string | null;
}) {
  if (!crd && !address && !effective && !details) return null;
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm text-[var(--text-dim,#475569)]">
      {crd ? (
        <>
          <dt className="pt-0.5 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">CRD #</dt>
          <dd className="text-[var(--text,#0f172a)]">{crd}</dd>
        </>
      ) : null}
      {address ? (
        <>
          <dt className="pt-0.5 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">Address</dt>
          <dd className="whitespace-pre-line">{address}</dd>
        </>
      ) : null}
      {effective ? (
        <>
          <dt className="pt-0.5 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">Effective</dt>
          <dd>{effective}</dd>
        </>
      ) : null}
      {details ? (
        <>
          <dt className="pt-0.5 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted,#94a3b8)]">Details</dt>
          <dd className="leading-6">{details}</dd>
        </>
      ) : null}
    </dl>
  );
}
