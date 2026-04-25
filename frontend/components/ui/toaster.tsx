"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { Toast, type ToastVariant } from "./toast";

// Module-level pub/sub. Lives outside React so callers can `enqueueToast`
// from anywhere (event handlers, fetch catch blocks, the useToast hook)
// without threading a context provider through the tree. The single
// <Toaster /> mount in app/(app)/layout.tsx subscribes; if the toaster
// isn't mounted, calls are silently dropped (intentional — the only valid
// caller surface is the authenticated app shell where we mount it).

export interface EnqueueToastInput {
  variant: ToastVariant;
  body: string;
  title?: string;
  durationMs?: number;
}

interface ActiveToast extends EnqueueToastInput {
  id: number;
}

type Listener = (toasts: ReadonlyArray<ActiveToast>) => void;

const listeners = new Set<Listener>();
let toasts: ReadonlyArray<ActiveToast> = [];
let nextId = 1;

function emit(): void {
  for (const listener of listeners) {
    listener(toasts);
  }
}

export function enqueueToast(input: EnqueueToastInput): number {
  const id = nextId++;
  toasts = [...toasts, { ...input, id }];
  emit();
  return id;
}

function dismissToast(id: number): void {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  listener(toasts);
  return () => {
    listeners.delete(listener);
  };
}

export function Toaster() {
  const [mounted, setMounted] = useState(false);
  const [active, setActive] = useState<ReadonlyArray<ActiveToast>>([]);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    return subscribe(setActive);
  }, []);

  if (!mounted || typeof document === "undefined") return null;

  return createPortal(
    <div
      aria-live="polite"
      className="pointer-events-none fixed right-4 top-4 z-[100] flex w-full max-w-sm flex-col gap-2"
    >
      {active.map((t) => (
        <Toast
          key={t.id}
          variant={t.variant}
          title={t.title}
          body={t.body}
          durationMs={t.durationMs}
          onDismiss={() => dismissToast(t.id)}
        />
      ))}
    </div>,
    document.body,
  );
}
