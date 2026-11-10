"use client";

/**
 * Questions bots asked Stef, grouped by agent, at the top of the Work tab.
 * Stef answers with an option button or free text; the onset manager bot
 * collects answers from its inbox. Answers can change until collected.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { apiFetch } from "@/utils/api";

const REFRESH_MS = 8_000;

export interface OfficeQuestion {
  id: string;
  agent: string;
  text: string;
  options: string[];
  allowText: boolean;
  link: string | null;
  status: "open" | "answered";
  answer: string | null;
  answeredAt: string | null;
  collected: boolean;
}

export function useOfficeQuestions(sessionId: string, enabled: boolean) {
  const url = `/api/v1/office-questions/${encodeURIComponent(sessionId)}`;
  const [result, setResult] = useState<{
    url: string;
    items: OfficeQuestion[];
  } | null>(null);
  const reload = useCallback(
    () =>
      apiFetch(url)
        .then((r) => (r.ok ? r.json() : []))
        .then((items: OfficeQuestion[]) => setResult({ url, items }))
        .catch(() => {}),
    [url],
  );
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const tick = () => {
      if (!cancelled) void reload();
    };
    tick();
    const timer = setInterval(tick, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled, reload]);
  return {
    items: enabled && result?.url === url ? result.items : [],
    reload,
  };
}

function QuestionRow({
  sessionId,
  q,
  onAnswered,
}: {
  sessionId: string;
  q: OfficeQuestion;
  onAnswered: () => void;
}): ReactNode {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = q.collected;

  const send = async (answer: string) => {
    if (!answer.trim() || busy || locked) return;
    setBusy(true);
    setError(null);
    try {
      const r = await apiFetch(
        `/api/v1/office-questions/${encodeURIComponent(sessionId)}/${encodeURIComponent(q.id)}/answer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answer: answer.trim() }),
        },
      );
      if (r.ok) {
        setText("");
        onAnswered();
      } else if (r.status === 409) {
        setError("onset already collected this answer.");
        onAnswered();
      } else if (r.status === 401 || r.status === 403) {
        setError("Only your own login can answer.");
      } else {
        setError("Couldn't save the answer.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-md bg-slate-950/70 border border-slate-800 p-2.5">
      <div className="text-[13px] text-white font-semibold mb-2">{q.text}</div>
      {q.answer && (
        <div className="text-xs mb-2 text-emerald-300">
          Answered: <span className="font-bold">{q.answer}</span>
          {locked ? (
            <span className="text-slate-500"> · collected by onset ✓</span>
          ) : (
            <span className="text-slate-500"> · you can still change it</span>
          )}
        </div>
      )}
      {q.options.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {q.options.map((o) => (
            <button
              key={o}
              disabled={busy || locked}
              onClick={() => void send(o)}
              className={`text-xs font-bold px-2.5 py-1 rounded-md border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                q.answer === o
                  ? "bg-emerald-600 border-emerald-500 text-white"
                  : "bg-slate-800 border-slate-700 text-slate-100 hover:bg-blue-600 hover:border-blue-500"
              }`}
            >
              {o}
            </button>
          ))}
        </div>
      )}
      {q.allowText && !locked && (
        <div className="flex gap-1.5 mb-1">
          <input
            value={text}
            maxLength={500}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void send(text);
            }}
            placeholder="Or type an answer…"
            className="flex-1 min-w-0 bg-slate-800 border border-slate-700 rounded-md px-2 py-1 text-xs text-white placeholder:text-slate-500 focus:outline-none focus:border-blue-500"
          />
          <button
            disabled={busy || !text.trim()}
            onClick={() => void send(text)}
            className="text-xs font-bold px-2.5 py-1 rounded-md bg-blue-500 hover:bg-blue-600 text-white disabled:opacity-40"
          >
            Send
          </button>
        </div>
      )}
      {q.link && (
        <a
          href={q.link}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block text-[11px] text-blue-300 hover:text-blue-200 underline mt-1"
        >
          Open ↗
        </a>
      )}
      {error && <div className="text-[11px] text-red-400 mt-1">{error}</div>}
    </div>
  );
}

/** One card per agent with its open/answered questions. */
export function OfficeQuestions({
  sessionId,
  items,
  nameOf,
  onAnswered,
}: {
  sessionId: string;
  items: OfficeQuestion[];
  nameOf: (agentKey: string) => string;
  onAnswered: () => void;
}): ReactNode {
  if (items.length === 0) return null;
  const byAgent = new Map<string, OfficeQuestion[]>();
  for (const q of items) {
    const list = byAgent.get(q.agent) ?? [];
    list.push(q);
    byAgent.set(q.agent, list);
  }
  return (
    <>
      {Array.from(byAgent.entries()).map(([agent, qs]) => {
        const waiting = qs.filter((q) => !q.answer).length;
        return (
          <div
            key={agent}
            className="rounded-lg border border-sky-500/50 bg-sky-950/20 p-2"
          >
            <div className="flex items-center gap-2 text-[11px] mb-2 px-1">
              <span className="font-bold text-sky-300">{nameOf(agent)}</span>
              <span className="text-slate-400">asks</span>
              {waiting > 0 && (
                <span className="ml-auto text-[10px] font-bold uppercase text-sky-300">
                  {waiting} waiting for you
                </span>
              )}
            </div>
            <div className="space-y-2">
              {qs.map((q) => (
                <QuestionRow
                  key={q.id}
                  sessionId={sessionId}
                  q={q}
                  onAnswered={onAnswered}
                />
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}
