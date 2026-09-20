"use client";

import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from "react";

export const METRIC_HELP_AUTO_CLOSE_MS = 6_000;

/**
 * Контекстная справка для метрик. На desktop открывается при hover/focus,
 * на touch-устройствах — по тапу с автоматическим закрытием.
 */
export function MetricHelp({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);
  const autoCloseRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tooltipId = useId();

  const clearAutoClose = useCallback(() => {
    if (autoCloseRef.current) {
      clearTimeout(autoCloseRef.current);
      autoCloseRef.current = null;
    }
  }, []);

  const close = useCallback(() => {
    clearAutoClose();
    setPinned(false);
    setOpen(false);
  }, [clearAutoClose]);

  const pinOpen = useCallback(() => {
    clearAutoClose();
    setPinned(true);
    setOpen(true);
    autoCloseRef.current = setTimeout(() => {
      autoCloseRef.current = null;
      setPinned(false);
      setOpen(false);
    }, METRIC_HELP_AUTO_CLOSE_MS);
  }, [clearAutoClose]);

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [close, open]);

  useEffect(() => clearAutoClose, [clearAutoClose]);

  return (
    <span
      ref={rootRef}
      className="relative inline-flex shrink-0 align-middle"
      onMouseEnter={() => {
        if (!pinned) setOpen(true);
      }}
      onMouseLeave={() => {
        if (!pinned) setOpen(false);
      }}
      onFocusCapture={() => {
        if (!pinned) setOpen(true);
      }}
      onBlurCapture={(event) => {
        if (!pinned && !event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        type="button"
        aria-label={`Справка: ${label}`}
        aria-expanded={open}
        aria-describedby={open ? tooltipId : undefined}
        title={`Что означает «${label}»`}
        className="flex h-5 w-5 cursor-help items-center justify-center rounded-full border border-gray-300 bg-white text-[11px] font-bold normal-case leading-none text-gray-500 transition hover:border-brand-400 hover:text-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-500/30 dark:border-zinc-600 dark:bg-zinc-800 dark:text-gray-400 dark:hover:border-brand-500 dark:hover:text-brand-400"
        onClick={() => {
          if (pinned) close();
          else pinOpen();
        }}
      >
        i
      </button>
      {open && (
        <span
          id={tooltipId}
          role="tooltip"
          className="pointer-events-none fixed inset-x-4 bottom-4 z-[70] rounded-xl border border-gray-200 bg-gray-950 px-4 py-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-white shadow-2xl md:absolute md:bottom-auto md:left-0 md:right-auto md:top-7 md:w-72 dark:border-zinc-600 dark:bg-zinc-950"
        >
          {children}
        </span>
      )}
    </span>
  );
}
