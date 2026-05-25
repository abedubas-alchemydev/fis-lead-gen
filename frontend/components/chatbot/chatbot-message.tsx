import clsx from "clsx";

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id: number;
  role: ChatRole;
  content: string;
}

export function ChatbotMessage({ role, content }: { role: ChatRole; content: string }) {
  const isUser = role === "user";
  return (
    <div className={clsx("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={clsx(
          "max-w-[80%] whitespace-pre-wrap break-words px-3 py-2 text-sm shadow-sm",
          isUser
            ? "rounded-2xl rounded-tr-md bg-[var(--accent,#6366f1)] text-white"
            : "rounded-2xl rounded-tl-md bg-[var(--surface-2,#f1f6fd)] text-[var(--text,#0f172a)]",
        )}
      >
        {content}
      </div>
    </div>
  );
}
