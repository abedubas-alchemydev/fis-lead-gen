"use client";

import { enqueueToast } from "./toaster";

// Caller-facing hook. Returns stable references — the underlying queue is a
// module-level pub/sub, so the helpers don't need useCallback memoization
// from React. Keep this surface intentionally narrow; add new helpers only
// when a real caller needs them.

export interface ToastOptions {
  title?: string;
  durationMs?: number;
}

export interface ToastApi {
  success: (body: string, opts?: ToastOptions) => void;
  error: (body: string, opts?: ToastOptions) => void;
  info: (body: string, opts?: ToastOptions) => void;
}

const api: ToastApi = {
  success: (body, opts) => {
    enqueueToast({ variant: "success", body, ...opts });
  },
  error: (body, opts) => {
    enqueueToast({ variant: "error", body, ...opts });
  },
  info: (body, opts) => {
    enqueueToast({ variant: "info", body, ...opts });
  },
};

export function useToast(): ToastApi {
  return api;
}
