"use client";

import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { getOutreachSignature, updateOutreachSignature } from "@/lib/api";

const MAX_SIGNATURE_LENGTH = 5000;

// Editor for the per-user outreach signature (footer). Lives on
// /settings/account. The compose surfaces (Outreach modal + the
// /outreach Create tab) prefill their editable Footer field from this
// value and append it beneath the body on send.
export function OutreachSignatureForm() {
  const toast = useToast();
  const [signature, setSignature] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await getOutreachSignature();
        if (cancelled || !isMountedRef.current) return;
        setSignature(result.signature);
      } catch {
        if (cancelled || !isMountedRef.current) return;
        setError("Couldn't load your saved signature. You can still set one below.");
      } finally {
        if (!cancelled && isMountedRef.current) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const result = await updateOutreachSignature(signature);
      if (!isMountedRef.current) return;
      setSignature(result.signature);
      toast.success(
        result.signature
          ? "Signature saved. New outreach drafts will prefill it."
          : "Signature cleared."
      );
    } catch (err) {
      if (!isMountedRef.current) return;
      setError(err instanceof Error ? err.message : "Unable to save your signature.");
    } finally {
      if (isMountedRef.current) setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label
          htmlFor="outreach-signature"
          className="block text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--text-muted,#94a3b8)]"
        >
          Signature
        </label>
        <textarea
          id="outreach-signature"
          value={signature}
          onChange={(e) => setSignature(e.target.value)}
          disabled={loading || saving}
          rows={6}
          maxLength={MAX_SIGNATURE_LENGTH}
          placeholder={
            loading
              ? "Loading…"
              : "Jane Doe\nDirector, Business Development\nAcme Clearing\n(555) 123-4567"
          }
          className="mt-2 block w-full resize-y rounded-[10px] border border-[var(--border,rgba(30,64,175,0.1))] bg-[var(--surface,#ffffff)] px-3 py-2 text-[13px] leading-6 text-[var(--text,#0f172a)] outline-none transition focus:border-[var(--accent,#6366f1)] focus:shadow-[0_0_0_3px_rgba(99,102,241,0.15)] disabled:cursor-not-allowed disabled:opacity-60"
        />
        <p className="mt-2 text-[11px] leading-5 text-[var(--text-dim,#475569)]">
          Appears beneath the body of every outreach email. It prefills the
          Footer field when you compose, where you can tweak it per-send.
          Leave blank for no footer.
        </p>
      </div>
      {error ? (
        <p className="rounded-2xl bg-red-50 px-4 py-3 text-sm text-danger">{error}</p>
      ) : null}
      <Button type="submit" disabled={loading || saving}>
        {saving ? "Saving..." : "Save signature"}
      </Button>
    </form>
  );
}
