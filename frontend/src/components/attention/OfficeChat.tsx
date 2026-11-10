"use client";

/**
 * Chat with one office agent. Stef's messages wait in the bot's inbox
 * (GET /api/v1/office/{id}/inbox/{agent}); the bot answers with the "reply"
 * field on its status update. Polls while open.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { apiFetch } from "@/utils/api";

const REFRESH_MS = 5_000;

interface ChatMessage {
  id: number;
  sender: "stef" | "bot";
  text: string;
  read: boolean;
  createdAt: string;
}

export function OfficeChat({
  sessionId,
  agentKey,
  agentName,
}: {
  sessionId: string;
  agentKey: string;
  agentName: string;
}): ReactNode {
  const url = `/api/v1/office-chat/${encodeURIComponent(sessionId)}/${encodeURIComponent(agentKey)}`;
  const [thread, setThread] = useState<{
    url: string;
    msgs: ChatMessage[];
  } | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const load = useCallback(
    () =>
      apiFetch(url)
        .then((r) => (r.ok ? r.json() : []))
        .then((msgs: ChatMessage[]) => setThread({ url, msgs }))
        .catch(() => {}),
    [url],
  );

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (!cancelled) void load();
    };
    tick();
    const timer = setInterval(tick, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [load]);

  const msgs = thread?.url === url ? thread.msgs : [];
  const count = msgs.length;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [count]);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    try {
      const r = await apiFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) {
        setError(
          r.status === 422
            ? "That looks like a password or key, not sent."
            : "Couldn't send.",
        );
      } else {
        setDraft("");
        await load();
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex flex-col min-h-0">
      <div className="text-[11px] uppercase tracking-wide text-neutral-500 mb-1">
        Chat with {agentName}
      </div>
      <div className="max-h-56 overflow-y-auto space-y-1.5 mb-2 pr-1">
        {msgs.length === 0 && (
          <div className="text-xs text-neutral-500 italic">
            No messages yet. Briefs you send wait in {agentName}&apos;s inbox
            until it checks in.
          </div>
        )}
        {msgs.map((m) => (
          <div
            key={m.id}
            className={`flex ${m.sender === "stef" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] whitespace-pre-wrap break-words rounded-lg px-2.5 py-1.5 text-[13px] leading-snug ${
                m.sender === "stef"
                  ? "bg-blue-600 text-white"
                  : "bg-neutral-800 text-neutral-100"
              }`}
            >
              {m.text}
              {m.sender === "stef" && (
                <div className="text-[10px] opacity-70 mt-0.5 text-right">
                  {m.read ? "picked up" : "waiting in inbox"}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="flex gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          rows={2}
          placeholder={`Brief ${agentName}…`}
          className="flex-1 resize-none bg-neutral-800 border border-neutral-700 rounded-lg px-2 py-1.5 text-[13px] text-white placeholder:text-neutral-500 focus:outline-none focus:border-blue-500"
        />
        <button
          onClick={() => void send()}
          disabled={sending || !draft.trim()}
          className="self-end bg-blue-500 hover:bg-blue-600 disabled:opacity-40 text-white text-xs font-bold py-1.5 px-3 rounded-lg"
        >
          Send
        </button>
      </div>
      {error && <div className="text-[11px] text-red-400 mt-1">{error}</div>}
    </div>
  );
}
