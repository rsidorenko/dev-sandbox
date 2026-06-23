"use client";

import { useState } from "react";
import Link from "next/link";
import { connectionPlatforms } from "./data";

/**
 * Блок пошаговых инструкций по подключению: переключатель платформ +
 * нумерованные шаги. Контент — ./data.ts. Стилистика переиспользована из
 * HowItWorks (нумерованные круги) и SubscriptionCard (табы/кнопки).
 */
export function InstructionsBlock() {
  const [activeId, setActiveId] = useState(connectionPlatforms[0].id);
  const active =
    connectionPlatforms.find((p) => p.id === activeId) ?? connectionPlatforms[0];

  return (
    <div>
      {/* Подсказка про ссылку подписки */}
      <div className="rounded-2xl bg-brand-50 p-6 dark:bg-brand-950/30">
        <h3 className="text-sm font-semibold text-brand-900 dark:text-brand-200">
          Где взять ссылку подписки?
        </h3>
        <p className="mt-2 text-sm text-brand-700 dark:text-brand-300">
          Ссылка подписки подтягивает все серверы автоматически. Скопируйте её в
          Личном кабинете в разделе «Ключи».
        </p>
        <Link
          href="/dashboard"
          className="mt-4 inline-block rounded-xl bg-brand-600 px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
        >
          Открыть Личный кабинет
        </Link>
      </div>

      {/* Табы платформ */}
      <div className="mt-8 flex flex-wrap gap-2">
        {connectionPlatforms.map((p) => {
          const isActive = p.id === active.id;
          return (
            <button
              key={p.id}
              onClick={() => setActiveId(p.id)}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                isActive
                  ? "bg-brand-600 text-white shadow-sm"
                  : "border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
              }`}
            >
              {p.emoji} {p.label}
            </button>
          );
        })}
      </div>

      {/* Шаги активной платформы */}
      <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
        <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
          {active.emoji} {active.label}
        </h2>

        <ol className="mt-6 space-y-6">
          {active.steps.map((step, i) => (
            <li key={i} className="flex gap-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-brand-100 text-base font-bold text-brand-700 dark:bg-brand-900/40 dark:text-brand-300">
                {i + 1}
              </div>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                  {step.title}
                </h3>
                <p className="mt-1 text-sm leading-relaxed text-gray-600 dark:text-gray-300">
                  {step.description}
                </p>
                {step.links && step.links.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {step.links.map((l) => (
                      <a
                        key={l.href}
                        href={l.href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-700"
                      >
                        {l.label}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
