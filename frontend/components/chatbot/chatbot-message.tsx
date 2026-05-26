import clsx from "clsx";

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id: number;
  role: ChatRole;
  content: string;
  pending?: boolean;
  error?: boolean;
}

function TypingDots() {
  return (
    <span
      role="status"
      aria-label="Doxie is typing"
      className="inline-flex items-center gap-1 py-0.5"
    >
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current opacity-60 [animation-delay:-0.3s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current opacity-60 [animation-delay:-0.15s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current opacity-60" />
    </span>
  );
}

export function ChatbotMessage({
  role,
  content,
  pending,
  error
}: {
  role: ChatRole;
  content: string;
  pending?: boolean;
  error?: boolean;
}) {
  const isUser = role === "user";
  return (
    <div className={clsx("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={clsx(
          "max-w-[80%] whitespace-pre-wrap break-words px-3 py-2 text-sm shadow-sm",
          isUser
            ? "rounded-2xl rounded-tr-md bg-[var(--accent,#6366f1)] text-white"
            : error
              ? "rounded-2xl rounded-tl-md bg-[var(--surface-2,#f1f6fd)] text-[var(--danger,#dc2626)]"
              : "rounded-2xl rounded-tl-md bg-[var(--surface-2,#f1f6fd)] text-[var(--text,#0f172a)]"
        )}
      >
        {pending ? <TypingDots /> : content}
      </div>
    </div>
  );
}
