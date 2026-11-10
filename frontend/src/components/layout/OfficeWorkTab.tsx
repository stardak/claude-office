"use client";

/**
 * Work tab: everything the office's agents have handed in (emails, plans,
 * reports, approval requests), newest first. Unlike the Events log this is
 * stored on the server, so it's there even if the page wasn't open.
 */

import { useEffect, useState, type ReactNode } from "react";
import { formatDistanceToNow } from "date-fns";
import { apiFetch } from "@/utils/api";
import {
  useGameStore,
  selectSessionId,
  selectAgents,
} from "@/stores/gameStore";
import type { OfficeWorkItem } from "@/components/attention/OfficeWorkPanel";

const REFRESH_MS = 10_000;

interface WorkRow extends OfficeWorkItem {
  agentKey: string;
}

export function isOfficeSession(sessionId: string): boolean {
  return /^[a-z0-9-]+_office$/.test(sessionId);
}

export function OfficeWorkTab(): ReactNode {
  const sessionId = useGameStore(selectSessionId);
  const agents = useGameStore(selectAgents);
  const [result, setResult] = useState<{
    sessionId: string;
    rows: WorkRow[];
  } | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  useEffect(() => {
    if (!isOfficeSession(sessionId)) return;
    let cancelled = false;
    const load = () =>
      apiFetch(`/api/v1/office-work/${encodeURIComponent(sessionId)}`)
        .then((r) => (r.ok ? r.json() : []))
        .then((rows: WorkRow[]) => {
          if (!cancelled) setResult({ sessionId, rows });
        })
        .catch(() => {});
    void load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionId]);

  const rows = result?.sessionId === sessionId ? result.rows : [];
  const bossName = sessionId.replace(/_office$/, "");
  const nameOf = (key: string) =>
    key === "boss" ? bossName : (agents.get(key)?.name ?? key);

  if (rows.length === 0) {
    return (
      <div className="h-full flex items-center justify-center p-6 text-center text-slate-500 text-xs italic font-mono">
        Nothing handed in yet. Bots appear here when they send a status with
        &quot;detail&quot;, a &quot;link&quot;, or ask for your OK.
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-2 space-y-2 bg-slate-950/40">
      {rows.map((w) => {
        const open = openId === w.id;
        return (
          <div
            key={w.id}
            className={`rounded-lg border ${
              w.needsApproval
                ? "border-orange-500/50 bg-orange-950/20"
                : "border-slate-700 bg-slate-900"
            }`}
          >
            <button
              onClick={() => setOpenId(open ? null : w.id)}
              className="w-full text-left px-3 py-2 flex flex-col gap-1"
            >
              <div className="flex items-center gap-2 text-[11px]">
                <span className="font-bold text-blue-300">
                  {nameOf(w.agentKey)}
                </span>
                <span className="text-slate-500">
                  {formatDistanceToNow(new Date(w.createdAt), {
                    addSuffix: true,
                  })}
                </span>
                {w.needsApproval && (
                  <span className="ml-auto text-[10px] font-bold uppercase text-orange-300">
                    Needs your OK
                  </span>
                )}
              </div>
              <div className="text-sm text-white font-semibold">{w.title}</div>
              {!open && w.detail && (
                <div className="text-xs text-slate-400 line-clamp-2">
                  {w.detail}
                </div>
              )}
              {!w.detail && !w.link && (
                <div className="text-[11px] text-slate-500 italic">
                  No content sent with this one.
                </div>
              )}
            </button>
            {open && (w.detail || w.link) && (
              <div className="px-3 pb-3">
                {w.detail && (
                  <div className="max-h-[50vh] overflow-auto whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-100 bg-slate-950 border border-slate-800 rounded-lg p-3">
                    {w.detail}
                  </div>
                )}
                {w.link && (
                  <a
                    href={w.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 inline-block bg-blue-500 hover:bg-blue-600 text-white text-xs font-bold py-1.5 px-3 rounded-lg"
                  >
                    Open {new URL(w.link).hostname} ↗
                  </a>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
