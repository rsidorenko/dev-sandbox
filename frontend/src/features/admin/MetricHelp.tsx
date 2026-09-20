"use client";

import type { ReactNode } from "react";

/**
 * Контекстная справка для метрик. На desktop открывается при hover/focus,
 * на touch-устройствах — по тапу на нативный disclosure <details>.
 */
export function MetricHelp({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="group relative inline-flex align-middle">
      <summary
        aria-label={`Справка: ${label}`}
        title={`Что означает «${label}»`}
        className="flex h-5 w-5 cursor-help list-none items-center justify-center rounded-full border border-gray-300 bg-white text-[11px] font-bold normal-case leading-none text-gray-500 transition hover:border-brand-400 hover:text-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-500/30 dark:border-zinc-600 dark:bg-zinc-800 dark:text-gray-400 dark:hover:border-brand-500 dark:hover:text-brand-400 [&::-webkit-details-marker]:hidden"
      >
        i
      </summary>
      <div
        role="tooltip"
        className="pointer-events-none fixed inset-x-4 bottom-4 z-[70] hidden rounded-xl border border-gray-200 bg-gray-950 px-4 py-3 text-left text-xs font-normal normal-case leading-relaxed tracking-normal text-white shadow-2xl group-open:block md:absolute md:bottom-auto md:left-0 md:right-auto md:top-7 md:w-72 md:group-focus-within:block md:group-hover:block dark:border-zinc-600 dark:bg-zinc-950"
      >
        {children}
      </div>
    </details>
  );
}
