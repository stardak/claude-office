"use client";

/**
 * Finished work an office agent handed in (email text, report, caption...),
 * stored by the company offices adapter (backend routes/onset.py) and shown
 * when Stef clicks an agent or an event in the log.
 */

import { useEffect, useState, type ReactNode } from "react";
import { apiFetch } from "@/utils/api";

/** Finished work an office agent handed in (see backend routes/onset.py). */
export interface OfficeWorkItem {
  id: number;
  title: string;
  status: string;
  needsApproval: boolean;
  detail: string | null;
  link: string | null;
  createdAt: string;
}

export function useOfficeWork(
  sessionId: string,
  agentKey: string | null,
): OfficeWorkItem[] {
  // Results are tagged with the key they were fetched for, so switching agents
  // never shows the previous agent's work (and no synchronous reset is needed).
  const key =
    agentKey && /_office$/.test(sessionId) ? `${sessionId}/${agentKey}` : null;
  const [result, setResult] = useState<{
    key: string;
    items: OfficeWorkItem[];
  } | null>(null);
  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    apiFetch(
      `/api/v1/office-work/${key.split("/").map(encodeURIComponent).join("/")}`,
    )
      .then((r) => (r.ok ? r.json() : []))
      .then((items: OfficeWorkItem[]) => {
        if (!cancelled) setResult({ key, items });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [key]);
  return key && result?.key === key ? result.items : [];
}

/** Agent key for an event/agent id: the boss is stored as "boss". */
export function officeWorkKey(
  agentId: string | null | undefined,
): string | null {
  if (!agentId) return null;
  return agentId === "main" || agentId === "boss" ? "boss" : agentId;
}

/** Latest finished work for one agent, with a picker for older items. */
export function OfficeWorkPanel({
  sessionId,
  agentKey,
}: {
  sessionId: string;
  agentKey: string | null;
}): ReactNode {
  const work = useOfficeWork(sessionId, agentKey);
  const [picked, setPicked] = useState<{
    agent: string | null;
    id: number;
  } | null>(null);
  const openId = picked?.agent === agentKey ? picked.id : null;
  const shown = work.find((w) => w.id === openId) ?? work[0] ?? null;
  if (!shown) return null;
  return (
    <div className="font-sans">
      <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1 font-mono">
        Finished work
      </div>
      {work.length > 1 && (
        <select
          className="mb-2 w-full bg-slate-800 text-slate-200 text-xs rounded-md px-2 py-1 border border-slate-700"
          value={shown.id}
          onChange={(e) =>
            setPicked({ agent: agentKey, id: Number(e.target.value) })
          }
        >
          {work.map((w) => (
            <option key={w.id} value={w.id}>
              {new Date(w.createdAt).toLocaleString()} · {w.title}
            </option>
          ))}
        </select>
      )}
      <div className="flex items-center gap-2 mb-2">
        <span className="text-white text-sm font-bold truncate flex-1">
          {shown.title}
        </span>
        {shown.needsApproval && (
          <span className="text-[10px] font-bold uppercase bg-orange-500/20 text-orange-300 border border-orange-500/40 rounded px-1.5 py-0.5">
            Needs your OK
          </span>
        )}
      </div>
      {shown.detail && (
        <div className="max-h-[50vh] overflow-auto whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-100 bg-slate-950 border border-slate-800 rounded-lg p-3">
          {shown.detail}
        </div>
      )}
      {shown.link && (
        <a
          href={shown.link}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-block bg-blue-500 hover:bg-blue-600 text-white text-xs font-bold py-1.5 px-3 rounded-lg transition-colors"
        >
          Open {new URL(shown.link).hostname} ↗
        </a>
      )}
    </div>
  );
}
