"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  connectionPlatforms,
  fetchAndroidApk,
  fetchWindowsExe,
  type AndroidApk,
  type WindowsExe,
} from "./data";

/**
 * Блок пошаговых инструкций по подключению: переключатель платформ +
 * (для iOS/Android/Mac) переключатель приложения Happ/Karing + нумерованные шаги.
 * Контент — ./data.ts. Стилистика переиспользована из HowItWorks (нумерованные
 * круги) и SubscriptionCard (табы/кнопки).
 */
export function InstructionsBlock() {
  const [activeId, setActiveId] = useState(connectionPlatforms[0].id);
  const active =
    connectionPlatforms.find((p) => p.id === activeId) ?? connectionPlatforms[0];
  // Активное приложение внутри платформы с развилкой (ios/android/mac).
  const [activeAppId, setActiveAppId] = useState<string | null>(null);
  // Latest self-hosted Karing APK, resolved from /downloads/latest-android.json.
  // null until resolved (or if unreachable) — Android Karing step 1 then falls
  // back to the GitHub link.
  const [androidApk, setAndroidApk] = useState<AndroidApk | null>(null);
  // Latest self-hosted Karing Windows .exe, resolved from
  // /downloads/latest-windows.json — null until resolved (or if unreachable),
  // Windows step 1 then falls back to the GitHub link.
  const [windowsExe, setWindowsExe] = useState<WindowsExe | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchAndroidApk().then((apk) => {
      if (!cancelled) setAndroidApk(apk);
    });
    fetchWindowsExe().then((exe) => {
      if (!cancelled) setWindowsExe(exe);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const apps = active.apps;
  // Fall back to the recommended (then first) app when the stored activeAppId
  // doesn't match any app in the current platform (e.g. after switching platform).
  const activeApp =
    apps?.find((a) => a.id === activeAppId) ??
    apps?.find((a) => a.recommended) ??
    apps?.[0];
  const steps = activeApp?.steps ?? active.steps ?? [];

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

      {/* Переключатель приложения (Happ / Karing) для платформ с развилкой */}
      {apps && apps.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {apps.map((a) => {
            const isActive = activeApp?.id === a.id;
            return (
              <button
                key={a.id}
                onClick={() => setActiveAppId(a.id)}
                className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                  isActive
                    ? "bg-brand-600 text-white shadow-sm"
                    : "border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
                }`}
              >
                {a.emoji} {a.label}
                {a.recommended ? " (рекомендуется)" : ""}
              </button>
            );
          })}
        </div>
      )}

      {/* Шаги активной платформы/приложения */}
      <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
        <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
          {active.emoji} {active.label}
        </h2>

        <ol className="mt-6 space-y-6">
          {steps.map((step, i) => {
            // Inject the always-latest download link into step 1: APK for
            // Android (Karing variant), .exe for Windows — both resolved from
            // /downloads/*.json.
            const links =
              active.id === "android" && activeApp?.id === "karing" && i === 0 && androidApk
                ? [
                    {
                      href: androidApk.url,
                      label: `Скачать Karing (APK v${androidApk.version})`,
                    },
                    ...(step.links ?? []),
                  ]
                : active.id === "win" && i === 0 && windowsExe
                  ? [
                      {
                        href: windowsExe.url,
                        label: `Скачать Karing для Windows (v${windowsExe.version})`,
                      },
                      ...(step.links ?? []),
                    ]
                  : step.links;
            return (
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
                  {links && links.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {links.map((l) => (
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
            );
          })}
        </ol>
      </div>
    </div>
  );
}
