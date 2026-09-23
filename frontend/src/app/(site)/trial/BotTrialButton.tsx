"use client";

import { botTrialUrl } from "@/shared/config/site";
import { reachGoal } from "@/shared/lib/analytics";

/** Кнопка ухода в бота за триалом; client — только ради цели Метрики. */
export function BotTrialButton({ label }: { label: string }) {
  return (
    <a
      href={botTrialUrl}
      target="_blank"
      rel="noopener noreferrer"
      onClick={() => reachGoal("trial_click")}
      className="rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
    >
      {label}
    </a>
  );
}
