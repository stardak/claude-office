"use client";

import { useEffect, useCallback, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useAttentionStore, selectFocusPopup } from "@/stores/attentionStore";
import { useGameStore, selectSessionId } from "@/stores/gameStore";
import { useTranslation } from "@/hooks/useTranslation";
import type { AgentAnimationState } from "@/stores/gameStore";
import { useOfficeWork } from "./OfficeWorkPanel";
import { OfficeChat } from "./OfficeChat";

const POPUP_WIDTH = 260;
const WORK_POPUP_WIDTH = 460;
const POPUP_MARGIN = 16;

export default function AgentPopup(): ReactNode {
  const focusPopup = useAttentionStore(selectFocusPopup);
  const closeFocusPopup = useAttentionStore((s) => s.closeFocusPopup);
  const focusAgentTerminal = useAttentionStore((s) => s.focusAgentTerminal);
  const agents = useGameStore((s) => s.agents);
  const boss = useGameStore((s) => s.boss);
  const sessionId = useGameStore(selectSessionId);
  const { t } = useTranslation();
  const isOffice = /_office$/.test(sessionId ?? "");
  const rawId = focusPopup?.agentId ?? null;
  const workKey =
    rawId === "boss" || rawId === "main" ? "boss" : (rawId ?? null);
  const work = useOfficeWork(sessionId ?? "", workKey);
  // Which older item is open; tagged with the agent so it resets on switch.
  const [picked, setPicked] = useState<{
    agent: string | null;
    id: number;
  } | null>(null);
  const openWorkId = picked?.agent === rawId ? picked.id : null;
  const setOpenWorkId = (id: number) => setPicked({ agent: rawId, id });

  const handleFocusTerminal = useCallback(() => {
    if (!sessionId) return;
    focusAgentTerminal(sessionId, focusPopup?.agentId ?? null);
  }, [sessionId, focusAgentTerminal, focusPopup]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") closeFocusPopup();
    },
    [closeFocusPopup],
  );

  useEffect(() => {
    if (focusPopup) {
      window.addEventListener("keydown", handleKeyDown);
      return () => window.removeEventListener("keydown", handleKeyDown);
    }
  }, [focusPopup, handleKeyDown]);

  if (!focusPopup) return null;

  const isBoss = focusPopup.agentId === "boss" || focusPopup.agentId === "main";
  const agent: AgentAnimationState | null = isBoss
    ? null
    : (agents.get(focusPopup.agentId) ?? null);

  // Viewport-clamped positioning (wider, taller when there's work to read)
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const roomy = isOffice || work.length > 0;
  const width = Math.min(
    roomy ? WORK_POPUP_WIDTH : POPUP_WIDTH,
    vw - 2 * POPUP_MARGIN,
  );
  const height = roomy ? Math.min(680, vh - 2 * POPUP_MARGIN) : 200;
  let x = focusPopup.screenX + 20;
  let y = focusPopup.screenY - 60;
  if (x + width > vw - POPUP_MARGIN) x = focusPopup.screenX - width - 20;
  if (x < POPUP_MARGIN) x = POPUP_MARGIN;
  if (y + height > vh - POPUP_MARGIN) y = vh - height - POPUP_MARGIN;
  if (y < POPUP_MARGIN) y = POPUP_MARGIN;
  const shownWork = work.find((w) => w.id === openWorkId) ?? work[0] ?? null;

  const bossName = isOffice
    ? (sessionId ?? "").replace(/_office$/, "")
    : "Boss";
  const displayName = isBoss ? bossName : (agent?.name ?? focusPopup.agentId);
  const displayColor = isBoss ? "#f59e0b" : (agent?.color ?? "#888");
  const displayState = isBoss
    ? boss.backendState
    : (agent?.backendState ?? "unknown");
  const displayTask = isBoss ? boss.currentTask : agent?.currentTask;
  const displayType = isBoss ? "lead" : (agent?.characterType ?? "subagent");
  const displayDesk = isBoss ? null : agent?.desk;

  const popup = (
    <div
      className="fixed inset-0 z-[90]"
      onClick={(e) => {
        if (e.target === e.currentTarget) closeFocusPopup();
      }}
    >
      <div className="absolute inset-0" />
      <div
        className="absolute bg-neutral-900 border border-neutral-700 rounded-xl shadow-2xl p-4 flex flex-col overflow-y-auto"
        style={{
          left: x,
          top: y,
          width,
          maxHeight: roomy ? height : undefined,
        }}
      >
        <div className="flex items-center gap-2 mb-3">
          <div
            className="w-3 h-3 rounded-full shrink-0"
            style={{ backgroundColor: displayColor }}
          />
          <span className="text-white font-bold text-sm truncate flex-1">
            {displayName}
          </span>
          {displayDesk !== null && (
            <span className="text-neutral-500 text-[11px]">
              {t("attention.popup.desk")} #{displayDesk}
            </span>
          )}
        </div>

        <div className="text-[12px] text-neutral-400 space-y-1 mb-3">
          <div>
            <span className="text-neutral-600">
              {t("attention.popup.state")}:
            </span>{" "}
            {displayState}
          </div>
          {displayTask && (
            <div className="truncate">
              <span className="text-neutral-600">
                {t("attention.popup.task")}:
              </span>{" "}
              {displayTask}
            </div>
          )}
          <div>
            <span className="text-neutral-600">
              {t("attention.popup.type")}:
            </span>{" "}
            {displayType}
          </div>
        </div>

        {shownWork && (
          <div className="mb-3 flex flex-col min-h-0 flex-1">
            <div className="text-[11px] uppercase tracking-wide text-neutral-500 mb-1">
              Finished work
            </div>
            {work.length > 1 && (
              <select
                className="mb-2 bg-neutral-800 text-neutral-200 text-xs rounded-md px-2 py-1 border border-neutral-700"
                value={shownWork.id}
                onChange={(e) => setOpenWorkId(Number(e.target.value))}
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
                {shownWork.title}
              </span>
              {shownWork.needsApproval && (
                <span className="text-[10px] font-bold uppercase bg-orange-500/20 text-orange-300 border border-orange-500/40 rounded px-1.5 py-0.5">
                  Needs your OK
                </span>
              )}
            </div>
            {shownWork.detail && (
              <div className="max-h-60 overflow-auto whitespace-pre-wrap break-words text-[13px] leading-relaxed text-neutral-200 bg-neutral-950 border border-neutral-800 rounded-lg p-3">
                {shownWork.detail}
              </div>
            )}
            {shownWork.link && (
              <a
                href={shownWork.link}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 text-center bg-blue-500 hover:bg-blue-600 text-white text-xs font-bold py-1.5 px-3 rounded-lg transition-colors"
              >
                Open {new URL(shownWork.link).hostname} ↗
              </a>
            )}
          </div>
        )}

        {isOffice && workKey && (
          <div className="mb-3">
            <OfficeChat
              sessionId={sessionId ?? ""}
              agentKey={workKey}
              agentName={displayName}
            />
          </div>
        )}

        <div className="flex gap-2">
          {!isOffice && (
            <button
              onClick={handleFocusTerminal}
              className="flex-1 bg-blue-500 hover:bg-blue-600 text-white text-xs font-bold py-1.5 px-3 rounded-lg transition-colors"
            >
              {t("attention.popup.focusTerminal")}
            </button>
          )}
          <button
            onClick={closeFocusPopup}
            className="bg-neutral-800 hover:bg-neutral-700 text-neutral-400 text-xs font-bold py-1.5 px-3 rounded-lg transition-colors"
          >
            {t("attention.popup.close")}
          </button>
        </div>
      </div>
    </div>
  );

  if (typeof document === "undefined") return null;
  return createPortal(popup, document.body);
}
