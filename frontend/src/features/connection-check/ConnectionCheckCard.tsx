"use client";

/**
 * Карточка автоматической проверки подключения: статус по IP (трафик идёт через
 * наши серверы?), замер задержки/скорости и кнопки вердикта с причинами проблем.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckStatus,
  checkApi,
  detectPlatformFromUA,
  medianLatency,
} from "@/entities/connection-check/api";
import { siteConfig } from "@/shared/config/site";

type Phase =
  | "loading"
  | "running"
  | "verdict"
  | "picking-reason"
  | "thanks-ok"
  | "thanks-problem"
  | "disabled"
  | "not_found"
  | "expired"
  | "rate_limited"
  | "error";

/** Те же закрытые коды причин, что в боте (trial_feedback.connection_problem). */
const PROBLEM_REASONS: ReadonlyArray<readonly [string, string]> = [
  ["install_failed", "📲 Приложение не установилось"],
  ["import_failed", "🔗 Подписка не добавилась"],
  ["connect_failed", "🔌 VPN не подключается"],
  ["traffic_failed", "🌐 Подключилось, но интернет не работает"],
  ["too_hard", "🧩 Слишком сложно"],
  ["other", "💬 Другая причина"],
];

const SPEED_TEST_BYTES = 262144;
const PING_COUNT = 3;

function formatMbps(kbps: number | null): string {
  if (kbps === null) return "—";
  if (kbps >= 1000) return `${(kbps / 1000).toFixed(1)} Мбит/с`;
  return `${kbps} кбит/с`;
}

function formatLatency(ms: number | null): string {
  return ms === null ? "—" : `≈ ${ms} мс`;
}

export function ConnectionCheckCard({ token }: { token: string }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [status, setStatus] = useState<CheckStatus | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [throughput, setThroughput] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const runId = useRef(0);

  const runCheck = useCallback(async () => {
    const currentRun = ++runId.current;
    setPhase("loading");
    setLatency(null);
    setThroughput(null);
    setStatus(null);

    const statusRes = await checkApi.status(token);
    if (currentRun !== runId.current) return;
    if (!statusRes.ok) {
      if (statusRes.error === "not_found") setPhase("not_found");
      else if (statusRes.error === "subscription_expired") setPhase("expired");
      else if (statusRes.error === "rate_limited") setPhase("rate_limited");
      else setPhase("error");
      return;
    }
    const info = statusRes.data;
    setStatus(info);
    if (!info.enabled) {
      setPhase("disabled");
      return;
    }

    setPhase("running");
    // Задержка: 3 последовательных ping-а, медиана сглаживает первый холодный.
    const samples: Array<number | null> = [];
    for (let i = 0; i < PING_COUNT; i += 1) {
      // eslint-disable-next-line no-await-in-loop -- последовательный замер намеренный
      samples.push(await checkApi.ping());
      if (currentRun !== runId.current) return;
    }
    const median = medianLatency(samples);
    const speed = await checkApi.speed(SPEED_TEST_BYTES);
    if (currentRun !== runId.current) return;
    setLatency(median);
    setThroughput(speed);

    // Результат пишем даже с null-метриками: connected сервер определил сам.
    await checkApi.result(token, {
      latency_ms: median,
      throughput_kbps: speed,
      platform: detectPlatformFromUA(navigator.userAgent),
    });
    if (currentRun !== runId.current) return;
    setPhase("verdict");
  }, [token]);

  useEffect(() => {
    void runCheck();
  }, [runCheck]);

  const sendFeedback = useCallback(
    async (verdict: "ok" | "problem", reasonCode?: string) => {
      setSending(true);
      await checkApi.feedback(token, {
        verdict,
        reason_code: reasonCode,
        platform: detectPlatformFromUA(navigator.userAgent),
      });
      setSending(false);
      setPhase(verdict === "ok" ? "thanks-ok" : "thanks-problem");
    },
    [token],
  );

  const connected = status?.connected ?? null;
  const serverLine = connected && status?.server_label ? status.server_label : "";

  return (
    <div className="rounded-2xl border border-gray-100 bg-white p-8 text-center shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
      {(phase === "loading" || phase === "running") && (
        <>
          <div className="mx-auto h-10 w-10 animate-spin rounded-full border-4 border-brand-200 border-t-brand-600" />
          <h1 className="mt-5 text-xl font-bold text-gray-900 dark:text-gray-100">
            {phase === "loading" ? "Проверяем подключение…" : "Измеряем задержку и скорость…"}
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Это займёт несколько секунд
          </p>
        </>
      )}

      {(phase === "verdict" || phase === "picking-reason") && (
        <>
          {connected === true && (
            <>
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-2xl dark:bg-emerald-900/40">
                ✅
              </div>
              <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
                VPN подключён
              </h1>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                {serverLine
                  ? `Трафик идёт через сервер ${serverLine}`
                  : "Трафик идёт через наш сервер"}
              </p>
            </>
          )}
          {connected === false && (
            <>
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 text-2xl dark:bg-amber-900/40">
                ⚠️
              </div>
              <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
                VPN не обнаружен
              </h1>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Включите VPN в приложении и нажмите «Проверить снова». Если VPN уже
                включён — часть трафика может идти мимо туннеля (например, по IPv6).
              </p>
            </>
          )}
          {connected === null && (
            <>
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-gray-100 text-2xl dark:bg-zinc-700">
                ❔
              </div>
              <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
                Не удалось проверить автоматически
              </h1>
              <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Сервис временно не может сопоставить ваш адрес с серверами. Нажмите
                «Проверить снова» чуть позже.
              </p>
            </>
          )}

          <div className="mt-6 grid grid-cols-2 gap-3 text-left">
            <div className="rounded-xl border border-gray-100 p-4 dark:border-zinc-700">
              <div className="text-xs uppercase tracking-wide text-gray-400">Задержка</div>
              <div className="mt-1 text-lg font-semibold text-gray-900 dark:text-gray-100">
                {formatLatency(latency)}
              </div>
            </div>
            <div className="rounded-xl border border-gray-100 p-4 dark:border-zinc-700">
              <div className="text-xs uppercase tracking-wide text-gray-400">Скорость</div>
              <div className="mt-1 text-lg font-semibold text-gray-900 dark:text-gray-100">
                {formatMbps(throughput)}
              </div>
            </div>
          </div>

          {phase === "verdict" && (
            <div className="mt-6 flex flex-col gap-2">
              <div className="grid grid-cols-2 gap-2">
                <button
                  disabled={sending}
                  onClick={() => void sendFeedback("ok")}
                  className="rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-60"
                >
                  ✅ Всё работает
                </button>
                <button
                  disabled={sending}
                  onClick={() => setPhase("picking-reason")}
                  className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 disabled:opacity-60 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-700"
                >
                  ⚠️ Есть проблема
                </button>
              </div>
              <button
                onClick={() => void runCheck()}
                className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-700"
              >
                🔄 Проверить снова
              </button>
            </div>
          )}

          {phase === "picking-reason" && (
            <div className="mt-6 flex flex-col gap-2">
              <p className="text-sm font-medium text-gray-700 dark:text-zinc-200">
                Что именно не работает?
              </p>
              {PROBLEM_REASONS.map(([code, label]) => (
                <button
                  key={code}
                  disabled={sending}
                  onClick={() => void sendFeedback("problem", code)}
                  className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 disabled:opacity-60 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-700"
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {(phase === "thanks-ok" || phase === "thanks-problem") && (
        <>
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-2xl dark:bg-emerald-900/40">
            🙏
          </div>
          <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
            Спасибо!
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            {phase === "thanks-ok"
              ? "Рад работать для вас. Если что-то изменится — возвращайтесь на эту страницу."
              : "Мы уже видим, что именно не работает, и займёмся этим. Поддержка тоже на связи."}
          </p>
          <div className="mt-6 flex flex-col gap-2">
            <a
              href={siteConfig.supportTelegram}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
            >
              💬 Написать в поддержку
            </a>
            <button
              onClick={() => void runCheck()}
              className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:bg-zinc-700"
            >
              🔄 Проверить снова
            </button>
          </div>
        </>
      )}

      {phase === "disabled" && (
        <>
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-gray-100 text-2xl dark:bg-zinc-700">
            🧪
          </div>
          <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
            Проверка подключения временно отключена
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Если VPN не работает — напишите в поддержку, поможем настроить.
          </p>
          <div className="mt-6 flex flex-col gap-2">
            <a
              href={siteConfig.supportTelegram}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
            >
              💬 Написать в поддержку
            </a>
          </div>
        </>
      )}

      {phase === "not_found" && (
        <>
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 text-2xl dark:bg-amber-900/40">
            🔗
          </div>
          <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
            Ссылка недействительна
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Проверьте ссылку или запросите новую в Telegram-боте.
          </p>
        </>
      )}

      {phase === "expired" && (
        <>
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 text-2xl dark:bg-amber-900/40">
            ⏳
          </div>
          <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
            Подписка истекла
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Проверка доступна, пока действует пробный период или подписка.
          </p>
        </>
      )}

      {(phase === "rate_limited" || phase === "error") && (
        <>
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-100 text-2xl dark:bg-red-900/40">
            ⚠️
          </div>
          <h1 className="mt-4 text-xl font-bold text-gray-900 dark:text-gray-100">
            {phase === "rate_limited" ? "Слишком много проверок" : "Не удалось выполнить проверку"}
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            {phase === "rate_limited"
              ? "Подождите минуту и попробуйте снова."
              : "Что-то пошло не так. Попробуйте ещё раз."}
          </p>
          <div className="mt-6 flex flex-col gap-2">
            <button
              onClick={() => void runCheck()}
              className="rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
            >
              Повторить
            </button>
          </div>
        </>
      )}
    </div>
  );
}
