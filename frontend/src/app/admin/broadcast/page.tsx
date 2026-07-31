"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { adminApi } from "@/features/admin/api";
import {
  adminBtnPrimary,
  adminBtnSecondary,
  adminInput,
  adminMono,
  adminNum,
  adminRow,
  adminTable,
  adminTd,
  adminTh,
  adminTableWrap,
} from "@/features/admin/ui";
import {
  BROADCAST_CATEGORY_LABELS,
  type BroadcastCategory,
  type BroadcastEstimate,
  type BroadcastJob,
  type BroadcastJobStatus,
} from "@/entities/admin/types";
import { Alert } from "@/shared/ui/Alert";

const TERMINAL_STATUSES: BroadcastJobStatus[] = ["completed", "failed", "interrupted", "cancelled"];

const STATUS_LABEL: Record<BroadcastJobStatus, string> = {
  queued: "в очереди",
  running: "идёт отправка",
  completed: "завершена",
  failed: "ошибка",
  interrupted: "прервана",
  cancelled: "отменена",
};

function statusBadgeClass(status: BroadcastJobStatus): string {
  if (status === "completed") return "bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-400";
  if (status === "running" || status === "queued") return "bg-blue-100 text-blue-700 dark:bg-blue-950/40 dark:text-blue-400";
  if (status === "failed" || status === "interrupted") return "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-400";
  return "bg-gray-100 text-gray-500 dark:bg-zinc-700 dark:text-gray-400";
}

/** Превью HTML в стиле Telegram: allowlist тегов, безопасные схемы ссылок. */
function renderPreviewHtml(html: string): string {
  if (typeof window === "undefined") return html;
  const doc = new DOMParser().parseFromString(html, "text/html");
  const allowed = new Set([
    "B", "STRONG", "I", "EM", "U", "INS", "S", "STRIKE", "DEL", "CODE", "PRE", "BLOCKQUOTE", "A", "BR",
  ]);
  doc.body.querySelectorAll("*").forEach((el) => {
    if (!allowed.has(el.tagName)) {
      el.replaceWith(...Array.from(el.childNodes));
      return;
    }
    Array.from(el.attributes).forEach((attr) => {
      if (!(el.tagName === "A" && attr.name === "href")) el.removeAttribute(attr.name);
    });
    if (el.tagName === "A") {
      const href = el.getAttribute("href") || "";
      if (!/^(https?:|mailto:|tg:)/i.test(href)) el.removeAttribute("href");
    }
  });
  return doc.body.innerHTML;
}

export default function AdminBroadcastPage() {
  const [selected, setSelected] = useState<Set<BroadcastCategory>>(new Set());
  const [explicitRaw, setExplicitRaw] = useState("");
  const [message, setMessage] = useState("");
  const [disablePreview, setDisablePreview] = useState(false);

  const [estimating, setEstimating] = useState(false);
  const [estimate, setEstimate] = useState<BroadcastEstimate | null>(null);
  const [estimateError, setEstimateError] = useState("");

  const [actionError, setActionError] = useState("");
  const [actionInfo, setActionInfo] = useState("");
  const [busy, setBusy] = useState(false);

  const [activeJob, setActiveJob] = useState<BroadcastJob | null>(null);
  const [jobs, setJobs] = useState<BroadcastJob[]>([]);

  const taRef = useRef<HTMLTextAreaElement>(null);

  const explicitTargets = useMemo(
    () =>
      explicitRaw
        .split(/[\s,]+/)
        .map((s) => s.trim())
        .filter(Boolean),
    [explicitRaw],
  );

  const selectedArr = useMemo(() => Array.from(selected), [selected]);

  // Авто-оценка аудитории (debounce) при изменении категорий/явных целей.
  const runEstimate = useCallback(
    async (cats: string[], targets: string[]) => {
      setEstimating(true);
      setEstimateError("");
      const r = await adminApi.broadcastEstimate({ categories: cats, explicit_targets: targets });
      if (r.ok) {
        setEstimate(r.data);
      } else if (r.error === "unauthorized") {
        window.location.href = `/admin/login?next=${encodeURIComponent("/admin/broadcast")}`;
      } else if (r.error === "invalid_categories") {
        setEstimateError("Неверный набор категорий.");
      } else {
        setEstimateError("Не удалось оценить аудиторию.");
      }
      setEstimating(false);
    },
    [],
  );

  useEffect(() => {
    const id = window.setTimeout(() => {
      void runEstimate(selectedArr, explicitTargets);
    }, 350);
    return () => window.clearTimeout(id);
  }, [selectedArr, explicitTargets, runEstimate]);

  const loadJobs = useCallback(async () => {
    const r = await adminApi.listBroadcastJobs({ limit: 20 });
    if (r.ok) setJobs(r.data.jobs);
  }, []);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  // Поллинг активной задачи до терминального статуса.
  useEffect(() => {
    if (!activeJob || TERMINAL_STATUSES.includes(activeJob.status)) return;
    const id = window.setInterval(async () => {
      const r = await adminApi.broadcastJobStatus(activeJob.job_id);
      if (r.ok) {
        setActiveJob(r.data.job);
        if (TERMINAL_STATUSES.includes(r.data.job.status)) {
          void loadJobs();
        }
      }
    }, 2000);
    return () => window.clearInterval(id);
  }, [activeJob, loadJobs]);

  const toggleCategory = (key: BroadcastCategory) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // --- Тулбар форматирования: обернуть выделение в HTML-теги ---
  const wrapSelection = (open: string, close: string) => {
    const ta = taRef.current;
    const value = message;
    if (!ta) {
      setMessage(value + open + "текст" + close);
      return;
    }
    const start = ta.selectionStart ?? value.length;
    const end = ta.selectionEnd ?? value.length;
    const inner = value.slice(start, end) || "текст";
    const next = value.slice(0, start) + open + inner + close + value.slice(end);
    setMessage(next);
    window.requestAnimationFrame(() => {
      ta.focus();
      const s = start + open.length;
      ta.setSelectionRange(s, s + inner.length);
    });
  };

  const insertLink = () => {
    const url = window.prompt("Адрес ссылки (https://…)", "https://");
    if (!url) return;
    const safe = url.replace(/"/g, "%22");
    wrapSelection(`<a href="${safe}">`, "</a>");
  };

  const onTest = async () => {
    setActionError("");
    setActionInfo("");
    const raw = window.prompt("Ваш Telegram id (число) — куда отправить тестовое сообщение:", "");
    if (!raw) return;
    const chatId = Number(raw.trim());
    if (!Number.isFinite(chatId) || chatId <= 0) {
      setActionError("Некорректный Telegram id.");
      return;
    }
    if (!message.trim()) {
      setActionError("Сначала введите текст сообщения.");
      return;
    }
    setBusy(true);
    const r = await adminApi.broadcastTest({ chat_id: chatId, message_text: message, disable_web_page_preview: disablePreview });
    setBusy(false);
    if (r.ok) {
      setActionInfo("Тестовое сообщение отправлено. Проверьте Telegram.");
    } else if (r.error === "invalid_html") {
      setActionError(`Невалидная HTML-разметка: ${r.detail ?? ""}`);
    } else if (r.error === "blocked_by_user") {
      setActionError("Не доставлено: чат недоступен (пользователь заблокировал бота).");
    } else if (r.error === "telegram_not_configured") {
      setActionError("Бот не сконфигурирован (BOT_TOKEN не задан).");
    } else {
      setActionError(`Не удалось отправить тест: ${r.error}`);
    }
  };

  const onSend = async () => {
    setActionError("");
    setActionInfo("");
    if (!message.trim()) {
      setActionError("Введите текст сообщения.");
      return;
    }
    if (selectedArr.length === 0 && explicitTargets.length === 0) {
      setActionError("Выберите хотя бы одну категорию или укажите явных получателей.");
      return;
    }
    const total = estimate?.total ?? 0;
    if (total === 0) {
      setActionError("Аудитория пуста — некому отправлять.");
      return;
    }
    if (!window.confirm(`Отправить рассылку ${total} получателям? Действие необратимо.`)) return;
    setBusy(true);
    const r = await adminApi.broadcastSend({
      categories: selectedArr,
      explicit_targets: explicitTargets,
      message_text: message,
      disable_web_page_preview: disablePreview,
    });
    setBusy(false);
    if (r.ok) {
      setActionInfo(`Рассылка запущена: ${r.data.total_recipients} получателей. job_id: ${r.data.job_id.slice(0, 8)}`);
      setActiveJob({
        job_id: r.data.job_id,
        created_at: new Date().toISOString(),
        created_by: "",
        status: "queued",
        categories: selectedArr,
        explicit_telegram_ids_count: explicitTargets.length,
        message_text: message,
        parse_mode: "HTML",
        total_recipients: r.data.total_recipients,
        sent_count: 0,
        failed_count: 0,
        blocked_count: 0,
        started_at: null,
        finished_at: null,
        last_error: null,
      });
      void loadJobs();
    } else if (r.error === "invalid_html") {
      setActionError(`Невалидная HTML-разметка: ${r.detail ?? ""}`);
    } else if (r.error === "no_recipients") {
      setActionError("Аудитория пуста.");
    } else if (r.error === "too_many_recipients") {
      setActionError(`Слишком много получателей (${r.detail ?? "превышен лимит"}).`);
    } else if (r.error === "broadcast_in_progress") {
      setActionError("Другая рассылка уже идёт. Дождитесь её завершения.");
    } else if (r.error === "telegram_not_configured") {
      setActionError("Бот не сконфигурирован (BOT_TOKEN не задан).");
    } else {
      setActionError(`Не удалось запустить рассылку: ${r.error}`);
    }
  };

  const total = estimate?.total ?? 0;
  const progress = activeJob && activeJob.total_recipients > 0
    ? Math.round(((activeJob.sent_count + activeJob.failed_count + activeJob.blocked_count) / activeJob.total_recipients) * 100)
    : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Рассылка</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Массовая отправка сообщений пользователям бота. Выберите аудиторию и отправьте — рассылка идёт в фоне.
        </p>
      </div>

      {actionError && <Alert className="mb-2" variant="error">{actionError}</Alert>}
      {actionInfo && <Alert className="mb-2" variant="info">{actionInfo}</Alert>}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* --- Левая колонка: форма --- */}
        <div className="space-y-6">
          {/* Аудитория */}
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Аудитория
            </h2>
            <div className="space-y-2">
              {BROADCAST_CATEGORY_LABELS.map(({ key, label }) => (
                <label key={key} className="flex cursor-pointer items-center justify-between gap-3 rounded-lg px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-zinc-700/50">
                  <span className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                    <input
                      type="checkbox"
                      checked={selected.has(key)}
                      onChange={() => toggleCategory(key)}
                      className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                    />
                    {label}
                  </span>
                  <span className={`${adminNum} text-xs text-gray-400 dark:text-gray-500`}>
                    {estimate?.per_category_counts?.[key] ?? "—"}
                  </span>
                </label>
              ))}
            </div>

            <div className="mt-4">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                Явные получатели
              </label>
              <textarea
                value={explicitRaw}
                onChange={(e) => setExplicitRaw(e.target.value)}
                rows={2}
                placeholder="Telegram id или email, каждый с новой строки (или через запятую)"
                className={`${adminInput} w-full font-mono`}
              />
              {estimate && estimate.explicit_unresolved.length > 0 && (
                <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
                  Не удалось разрешить: {estimate.explicit_unresolved.join(", ")}
                </p>
              )}
            </div>

            <div className="mt-4 flex items-baseline justify-between border-t border-gray-100 pt-3 dark:border-zinc-700">
              <span className="text-sm text-gray-500 dark:text-gray-400">
                {estimating ? "оценка…" : "Итого получателей:"}
              </span>
              <span className={`${adminNum} text-xl font-bold text-gray-900 dark:text-gray-100`}>{total}</span>
            </div>
          </section>

          {/* Сообщение */}
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Сообщение
            </h2>

            <div className="mb-2 flex flex-wrap gap-1.5">
              <button type="button" onClick={() => wrapSelection("<b>", "</b>")} className={adminBtnSecondary}>B</button>
              <button type="button" onClick={() => wrapSelection("<i>", "</i>")} className={`${adminBtnSecondary} italic`}>I</button>
              <button type="button" onClick={() => wrapSelection("<code>", "</code>")} className={`${adminBtnSecondary} font-mono`}>Моно</button>
              <button type="button" onClick={insertLink} className={adminBtnSecondary}>Ссылка</button>
            </div>

            <textarea
              ref={taRef}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={7}
              placeholder="Текст сообщения. Используйте тулбар для форматирования: моноширинный фрагмент (копируемый), ссылка…"
              className={`${adminInput} w-full`}
            />

            <label className="mt-2 flex cursor-pointer items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
              <input
                type="checkbox"
                checked={disablePreview}
                onChange={(e) => setDisablePreview(e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
              />
              Без предпросмотра ссылок (disable_web_page_preview)
            </label>

            <div className="mt-4 flex flex-wrap gap-2">
              <button type="button" onClick={onSend} disabled={busy} className={adminBtnPrimary}>
                Отправить рассылку
              </button>
              <button type="button" onClick={onTest} disabled={busy} className={adminBtnSecondary}>
                Тест себе…
              </button>
            </div>
          </section>
        </div>

        {/* --- Правая колонка: превью + прогресс --- */}
        <div className="space-y-6">
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Предпросмотр (как в Telegram)
            </h2>
            <div
              className={
                "min-h-24 rounded-lg bg-gray-50 p-3 text-sm text-gray-800 dark:bg-zinc-900/50 dark:text-gray-200 " +
                "[&_code]:rounded [&_code]:bg-gray-200 [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em] " +
                "dark:[&_code]:bg-zinc-700 [&_a]:font-medium [&_a]:text-brand-600 [&_a]:underline [&_b]:font-semibold [&_i]:italic " +
                "[&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-gray-200 [&_pre]:p-2 [&_pre]:font-mono [&_pre]:text-[0.85em] " +
                "dark:[&_pre]:bg-zinc-700"
              }
              dangerouslySetInnerHTML={{
                __html: message.trim() ? renderPreviewHtml(message) : "<span class='text-gray-400'>пусто…</span>",
              }}
            />
          </section>

          {activeJob && (
            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                  Прогресс рассылки
                </h2>
                <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${statusBadgeClass(activeJob.status)}`}>
                  {STATUS_LABEL[activeJob.status]}
                </span>
              </div>
              <div className="mb-2 h-2 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-zinc-700">
                <div className="h-full rounded-full bg-brand-600 transition-all" style={{ width: `${progress}%` }} />
              </div>
              <div className="grid grid-cols-4 gap-2 text-center text-sm">
                <div>
                  <div className={`${adminNum} text-lg font-bold text-gray-900 dark:text-gray-100`}>{activeJob.total_recipients}</div>
                  <div className="text-xs text-gray-400">всего</div>
                </div>
                <div>
                  <div className={`${adminNum} text-lg font-bold text-green-600 dark:text-green-400`}>{activeJob.sent_count}</div>
                  <div className="text-xs text-gray-400">доставлено</div>
                </div>
                <div>
                  <div className={`${adminNum} text-lg font-bold text-red-600 dark:text-red-400`}>{activeJob.failed_count}</div>
                  <div className="text-xs text-gray-400">ошибок</div>
                </div>
                <div>
                  <div className={`${adminNum} text-lg font-bold text-gray-500 dark:text-gray-400`}>{activeJob.blocked_count}</div>
                  <div className="text-xs text-gray-400">заблокировано</div>
                </div>
              </div>
              {activeJob.last_error && (
                <p className="mt-2 text-xs text-red-600 dark:text-red-400">Последняя ошибка: {activeJob.last_error}</p>
              )}
            </section>
          )}
        </div>
      </div>

      {/* --- Последние рассылки --- */}
      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
          Последние рассылки
        </h2>
        <div className={adminTableWrap}>
          <table className={adminTable}>
            <thead>
              <tr>
                <th className={adminTh}>Создана</th>
                <th className={adminTh}>Статус</th>
                <th className={`${adminTh} text-right`}>Доставлено</th>
                <th className={`${adminTh} text-right`}>Ошибок</th>
                <th className={`${adminTh} text-right`}>Заблок.</th>
                <th className={adminTh}>Аудитория</th>
                <th className={adminTh}>Сообщение</th>
              </tr>
            </thead>
            <tbody>
              {jobs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-gray-400">
                    Рассылок пока не было
                  </td>
                </tr>
              ) : (
                jobs.map((j) => (
                  <tr key={j.job_id} className={adminRow}>
                    <td className={`${adminTd} ${adminNum} text-gray-500 dark:text-gray-400`}>
                      {j.created_at ? new Date(j.created_at).toLocaleString("ru-RU") : "—"}
                    </td>
                    <td className={adminTd}>
                      <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${statusBadgeClass(j.status)}`}>
                        {STATUS_LABEL[j.status]}
                      </span>
                    </td>
                    <td className={`${adminTd} ${adminNum} text-right`}>
                      {j.sent_count}<span className="text-gray-400 dark:text-gray-500"> / {j.total_recipients}</span>
                    </td>
                    <td className={`${adminTd} ${adminNum} text-right`}>{j.failed_count}</td>
                    <td className={`${adminTd} ${adminNum} text-right`}>{j.blocked_count}</td>
                    <td className={`${adminTd} text-xs text-gray-500 dark:text-gray-400`}>
                      {j.categories.length > 0 ? j.categories.join(", ") : "—"}
                      {j.explicit_telegram_ids_count > 0 ? ` +${j.explicit_telegram_ids_count} явных` : ""}
                    </td>
                    <td className={`${adminTd} ${adminMono} max-w-xs truncate text-xs text-gray-500 dark:text-gray-400`} title={j.message_text}>
                      {j.message_text.replace(/<[^>]+>/g, "")}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
