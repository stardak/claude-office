/**
 * EventDetailModal - Modal showing full details of a clicked event log entry
 */

"use client";

import { useEffect, useCallback } from "react";
import { X } from "lucide-react";
import { format } from "date-fns";
import type { EventLogEntry } from "@/stores/gameStore";
import { useTranslation } from "@/hooks/useTranslation";
import { getEventTypeBadgeClasses } from "@/utils/event-type-styles";
import { useGameStore, selectSessionId } from "@/stores/gameStore";
import {
  OfficeWorkPanel,
  officeWorkKey,
} from "@/components/attention/OfficeWorkPanel";

interface EventDetailModalProps {
  event: EventLogEntry;
  onClose: () => void;
}

export function EventDetailModal({ event, onClose }: EventDetailModalProps) {
  const { t } = useTranslation();
  const sessionId = useGameStore(selectSessionId);
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose],
  );

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  const detail = event.detail ?? {};
  const hasDetail = Object.keys(detail).length > 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal Panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`${event.type.replace(/_/g, " ")} — ${event.summary}`}
        className="relative z-10 w-full max-w-2xl max-h-[80vh] flex flex-col bg-slate-900 border border-slate-700 rounded-xl shadow-2xl overflow-hidden font-mono text-xs"
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-700 bg-slate-950 flex-shrink-0">
          <span
            className={`px-2 py-0.5 rounded border text-[10px] font-bold uppercase tracking-wider ${getEventTypeBadgeClasses(event.type)}`}
          >
            {event.type.replace(/_/g, " ")}
          </span>
          <span className="text-slate-400 text-[11px]">
            {format(event.timestamp, "HH:mm:ss.SSS")}
          </span>
          {event.agentId && (
            <span className="text-blue-400 text-[10px] font-mono">
              @{event.agentId}
            </span>
          )}
          <button
            onClick={onClose}
            className="ml-auto p-1.5 text-slate-500 hover:text-white hover:bg-slate-700 rounded transition-colors"
            aria-label={t("modal.close")}
          >
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-grow overflow-y-auto p-4 space-y-3">
          {/* Summary */}
          <div>
            <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
              {t("eventDetail.summary")}
            </div>
            <div className="text-slate-200 text-[12px]">{event.summary}</div>
          </div>

          {/* Finished work handed in by an office agent (Onset offices) */}
          <OfficeWorkPanel
            sessionId={sessionId}
            agentKey={officeWorkKey(event.agentId)}
          />

          {/* Detail fields */}
          {hasDetail ? (
            <>
              {detail.toolName && (
                <div>
                  <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
                    {t("eventDetail.tool")}
                  </div>
                  <div className="text-amber-300 text-[12px]">
                    {String(detail.toolName)}
                  </div>
                </div>
              )}

              {detail.agentName && (
                <div>
                  <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
                    {t("eventDetail.agentName")}
                  </div>
                  <div className="text-blue-300 text-[12px]">
                    {String(detail.agentName)}
                  </div>
                </div>
              )}

              {detail.taskDescription && (
                <div>
                  <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
                    {t("eventDetail.taskDescription")}
                  </div>
                  <div className="text-slate-200 text-[12px] whitespace-pre-wrap leading-relaxed">
                    {String(detail.taskDescription)}
                  </div>
                </div>
              )}

              {detail.prompt && (
                <div>
                  <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
                    {t("eventDetail.userPrompt")}
                  </div>
                  <div className="text-cyan-200 text-[12px] whitespace-pre-wrap leading-relaxed bg-slate-800/50 rounded p-2 border border-slate-700">
                    {String(detail.prompt)}
                  </div>
                </div>
              )}

              {detail.thinking && (
                <div>
                  <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
                    {t("eventDetail.thinking")}
                  </div>
                  <div className="text-slate-400 italic text-[12px] whitespace-pre-wrap leading-relaxed bg-slate-800/30 rounded p-2 border border-slate-700/50">
                    {String(detail.thinking)}
                  </div>
                </div>
              )}

              {detail.message && (
                <div>
                  <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
                    {t("eventDetail.message")}
                  </div>
                  <div className="text-slate-200 text-[12px] whitespace-pre-wrap leading-relaxed">
                    {String(detail.message)}
                  </div>
                </div>
              )}

              {detail.resultSummary && (
                <div>
                  <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
                    {t("eventDetail.resultSummary")}
                  </div>
                  <div className="text-emerald-300 text-[12px] whitespace-pre-wrap leading-relaxed">
                    {String(detail.resultSummary)}
                  </div>
                </div>
              )}

              {detail.errorType && (
                <div>
                  <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
                    {t("eventDetail.errorType")}
                  </div>
                  <div className="text-red-400 text-[12px]">
                    {String(detail.errorType)}
                  </div>
                </div>
              )}

              {detail.toolInput && (
                <div>
                  <div className="text-slate-500 text-[10px] uppercase tracking-widest mb-1">
                    {t("eventDetail.toolInput")}
                  </div>
                  <pre className="text-slate-300 text-[11px] bg-slate-800 rounded p-3 border border-slate-700 overflow-x-auto whitespace-pre-wrap break-words leading-relaxed">
                    {JSON.stringify(detail.toolInput, null, 2)}
                  </pre>
                </div>
              )}
            </>
          ) : (
            <div className="text-slate-600 italic text-center py-4">
              {t("eventDetail.noDetail")}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 px-4 py-3 border-t border-slate-700 bg-slate-950 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 text-xs font-bold rounded transition-colors"
          >
            {t("modal.close")}
          </button>
        </div>
      </div>
    </div>
  );
}
