// Cross-tree messaging for the Doxie widget. The widget mounts once in the
// app layout while "Ask Doxie" entry points live deep inside page trees —
// a window CustomEvent is the lightest bridge (no store, no provider, and
// senders don't need the widget mounted to compile).

export const DOXIE_ASK_EVENT = "doxie:ask";

export interface DoxieAskDetail {
  // Text to pre-fill in the chat input. Deliberately NOT auto-sent: the
  // user reviews and presses Send themselves, which matters now that
  // Doxie has action tools registered server-side.
  prompt: string;
}

export function askDoxie(detail: DoxieAskDetail): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<DoxieAskDetail>(DOXIE_ASK_EVENT, { detail }),
  );
}
