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
  BROADCAST_ACTION_BUTTONS,
  BROADCAST_CATEGORY_LABELS,
  type BroadcastActionButton,
  type BroadcastBonusConfig,
  type BroadcastCategory,
  type BroadcastEstimate,
  type BroadcastJob,
  type BroadcastJobStatus,
  type BroadcastUrlButton,
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
  const [actionBtns, setActionBtns] = useState<Set<BroadcastActionButton>>(new Set());
  const [urlButtons, setUrlButtons] = useState<BroadcastUrlButton[]>([]);

  // Бонус-кнопка: получатель жмёт → бесплатно получает N дней (3..30) ИЛИ авто-применяется промокод.
  const [bonusEnabled, setBonusEnabled] = useState(false);
  const [bonusType, setBonusType] = useState<"days" | "promo">("days");
  const [bonusDaysRaw, setBonusDaysRaw] = useState("3");
  const [promoCodeRaw, setPromoCodeRaw] = useState("");
  const [promoOptions, setPromoOptions] = useState<string[]>([]);
  const [bonusLabel, setBonusLabel] = useState("");
  const [showBonusAdvanced, setShowBonusAdvanced] = useState(false);
  const [bonusTtlRaw, setBonusTtlRaw] = useState("30");
  const [bonusWarnings, setBonusWarnings] = useState<string[]>([]);

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

  const buttonsPayload = useMemo(() => {
    const actions = Array.from(actionBtns);
    const urls = urlButtons.filter((b) => b.label.trim() && b.url.trim());
    if (actions.length === 0 && urls.length === 0) return undefined;
    return { action_buttons: actions, url_buttons: urls };
  }, [actionBtns, urlButtons]);

  // Бонус-конфиг для send/test. null, если выключено или невалидно — тогда кнопка не отправляется.
  const bonusPayload = useMemo<BroadcastBonusConfig | null>(() => {
    if (!bonusEnabled) return null;
    const label = bonusLabel.trim();
    if (!label || label.length > 64) return null;
    const ttl = Number.parseInt(bonusTtlRaw, 10);
    const ttl_days = Number.isFinite(ttl) && ttl >= 1 ? ttl : 30;
    if (bonusType === "promo") {
      const code = promoCodeRaw.trim().toUpperCase();
      if (!code || code.length < 3 || code.length > 32 || !/^[A-Z0-9]+$/.test(code)) return null;
      return { type: "promo", code, label, ttl_days };
    }
    const days = Number.parseInt(bonusDaysRaw, 10);
    if (!Number.isFinite(days) || days < 3 || days > 30) return null;
    return { type: "days", days, label, ttl_days };
  }, [bonusEnabled, bonusType, bonusDaysRaw, promoCodeRaw, bonusLabel, bonusTtlRaw]);

  // Подгрузка списка активных промокодов для подсказки (datalist) при выборе типа promo.
  useEffect(() => {
    if (!bonusEnabled || bonusType !== "promo" || promoOptions.length > 0) return;
    let cancelled = false;
    adminApi
      .listPromocodes()
      .then((r) => {
        if (r.ok && !cancelled) {
          setPromoOptions(r.data.promocodes.filter((p) => p.is_active).map((p) => p.code));
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [bonusEnabled, bonusType, promoOptions.length]);

  const previewButtons = useMemo(() => {
    const out: { text: string }[] = [];
    for (const key of Array.from(actionBtns)) {
      const meta = BROADCAST_ACTION_BUTTONS.find((b) => b.key === key);
      if (meta) out.push({ text: meta.label });
    }
    for (const b of urlButtons) {
      const t = b.label.trim();
      if (t) out.push({ text: t });
    }
    if (bonusPayload) out.push({ text: bonusPayload.label });
    return out;
  }, [actionBtns, urlButtons, bonusPayload]);

  const toggleActionButton = (key: BroadcastActionButton) => {
    setActionBtns((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

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

  const insertAtCursor = (snippet: string) => {
    const ta = taRef.current;
    const value = message;
    if (!ta) {
      setMessage(value + snippet);
      return;
    }
    const start = ta.selectionStart ?? value.length;
    const next = value.slice(0, start) + snippet + value.slice(start);
    setMessage(next);
    window.requestAnimationFrame(() => {
      ta.focus();
      ta.setSelectionRange(start + snippet.length, start + snippet.length);
    });
  };

  const onTest = async () => {
    setActionError("");
    setActionInfo("");
    setBonusWarnings([]);
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
    const r = await adminApi.broadcastTest({ chat_id: chatId, message_text: message, disable_web_page_preview: disablePreview, buttons: buttonsPayload, bonus: bonusPayload });
    setBusy(false);
    if (r.ok) {
      setBonusWarnings(r.data.bonus_warnings ?? []);
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
    setBonusWarnings([]);
    if (!message.trim()) {
      setActionError("Введите текст сообщения.");
      return;
    }
    if (selectedArr.length === 0 && explicitTargets.length === 0) {
      setActionError("Выберите хотя бы одну категорию или укажите явных получателей.");
      return;
    }
    if (bonusEnabled && !bonusPayload) {
      setActionError("Бонус-кнопка включена, но заполнена неверно.");
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
      buttons: buttonsPayload,
      bonus: bonusPayload,
    });
    setBusy(false);
    if (r.ok) {
      setBonusWarnings(r.data.bonus_warnings ?? []);
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
        buttons: [],
        bonus: bonusPayload,
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
                placeholder="Telegram id (для web-only — отрицательный), каждый с новой строки (или через запятую)"
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
              <button type="button" onClick={() => insertAtCursor("<br>")} className={adminBtnSecondary}>↵ Перенос</button>
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

          {/* Кнопки под сообщением */}
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
            <h2 className="mb-1 text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
              Кнопки под сообщением
            </h2>
            <p className="mb-3 text-xs text-gray-400 dark:text-gray-500">
              Клик по кнопке-действию откроет экран бота и заменит текст этого сообщения. URL-кнопки открывают ссылку и не меняют сообщение.
            </p>

            <div className="space-y-2">
              {BROADCAST_ACTION_BUTTONS.map(({ key, label }) => (
                <label
                  key={key}
                  className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-zinc-700/50"
                >
                  <input
                    type="checkbox"
                    checked={actionBtns.has(key)}
                    onChange={() => toggleActionButton(key)}
                    className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                  />
                  <span className="text-sm text-gray-700 dark:text-gray-300">{label}</span>
                </label>
              ))}
            </div>

            <div className="mt-4">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm font-medium text-gray-700 dark:text-gray-300">URL-кнопки</span>
                <button
                  type="button"
                  onClick={() => setUrlButtons((prev) => [...prev, { label: "", url: "" }])}
                  className="text-xs font-semibold text-brand-600 transition hover:text-brand-700"
                >
                  + добавить ссылку
                </button>
              </div>
              <div className="space-y-2">
                {urlButtons.length === 0 && (
                  <p className="text-xs text-gray-400 dark:text-gray-500">Нет ссылок. Например: «Наш сайт» → https://bravada-connect.ru</p>
                )}
                {urlButtons.map((b, i) => (
                  <div key={i} className="flex flex-col gap-2 sm:flex-row">
                    <input
                      type="text"
                      value={b.label}
                      onChange={(e) =>
                        setUrlButtons((prev) => prev.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))
                      }
                      placeholder="Текст"
                      className={`${adminInput} w-full sm:w-1/3`}
                    />
                    <input
                      type="url"
                      value={b.url}
                      onChange={(e) =>
                        setUrlButtons((prev) => prev.map((x, j) => (j === i ? { ...x, url: e.target.value } : x)))
                      }
                      placeholder="https://…"
                      className={`${adminInput} w-full sm:flex-1`}
                    />
                    <button
                      type="button"
                      onClick={() => setUrlButtons((prev) => prev.filter((_, j) => j !== i))}
                      className="self-end rounded-lg px-2 py-2 text-sm font-semibold text-red-600 transition hover:text-red-700 sm:self-auto"
                      aria-label="Удалить ссылку"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* Бонус-кнопка (бесплатные дни или промокод) */}
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-500 dark:text-gray-400">
                  Бонус-кнопка
                </h2>
                <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
                  Кнопка под сообщением: «получить дни» (3–30) или авто-применение промокода. Срабатывает только у реальных получателей рассылки.
                </p>
              </div>
              <label className="flex shrink-0 cursor-pointer items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                <input
                  type="checkbox"
                  checked={bonusEnabled}
                  onChange={(e) => setBonusEnabled(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500"
                />
                Вкл.
              </label>
            </div>

            {bonusEnabled && (
              <div className="mt-4 space-y-3">
                <div className="flex gap-4 text-sm">
                  <label className="flex cursor-pointer items-center gap-2 text-gray-700 dark:text-gray-300">
                    <input
                      type="radio"
                      name="bonusType"
                      checked={bonusType === "days"}
                      onChange={() => setBonusType("days")}
                      className="h-4 w-4 border-gray-300 text-brand-600 focus:ring-brand-500"
                    />
                    Бесплатные дни
                  </label>
                  <label className="flex cursor-pointer items-center gap-2 text-gray-700 dark:text-gray-300">
                    <input
                      type="radio"
                      name="bonusType"
                      checked={bonusType === "promo"}
                      onChange={() => setBonusType("promo")}
                      className="h-4 w-4 border-gray-300 text-brand-600 focus:ring-brand-500"
                    />
                    Промокод
                  </label>
                </div>

                <div className="flex flex-wrap items-end gap-3">
                  {bonusType === "days" ? (
                    <label className="block">
                      <span className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">Дней (3–30)</span>
                      <input
                        type="number"
                        min={3}
                        max={30}
                        value={bonusDaysRaw}
                        onChange={(e) => setBonusDaysRaw(e.target.value)}
                        className={`${adminInput} w-24`}
                      />
                    </label>
                  ) : (
                    <label className="block">
                      <span className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">Промокод</span>
                      <input
                        type="text"
                        list="promo-options"
                        value={promoCodeRaw}
                        onChange={(e) => setPromoCodeRaw(e.target.value.toUpperCase())}
                        maxLength={32}
                        placeholder="SUMMER15"
                        className={`${adminInput} w-40`}
                      />
                      <datalist id="promo-options">
                        {promoOptions.map((c) => (
                          <option key={c} value={c} />
                        ))}
                      </datalist>
                    </label>
                  )}
                  <label className="block flex-1 min-w-[12rem]">
                    <span className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">Текст кнопки</span>
                    <input
                      type="text"
                      value={bonusLabel}
                      onChange={(e) => setBonusLabel(e.target.value)}
                      maxLength={64}
                      placeholder={bonusType === "days" ? "🎁 Получить 3 дня бесплатно" : "🎟 Забрать скидку"}
                      className={`${adminInput} w-full`}
                    />
                  </label>
                </div>
                {bonusType === "days" &&
                  (() => {
                    const d = Number.parseInt(bonusDaysRaw, 10);
                    if (bonusDaysRaw !== "" && (!Number.isFinite(d) || d < 3 || d > 30)) {
                      return <p className="text-xs text-amber-600 dark:text-amber-400">Число дней должно быть от 3 до 30.</p>;
                    }
                    return null;
                  })()}
                {bonusType === "promo" &&
                  promoCodeRaw.trim() !== "" &&
                  !/^[A-Z0-9]{3,32}$/.test(promoCodeRaw.trim().toUpperCase()) && (
                    <p className="text-xs text-amber-600 dark:text-amber-400">Код — 3–32 символа (латиница и цифры).</p>
                  )}
                <button
                  type="button"
                  onClick={() => setShowBonusAdvanced((v) => !v)}
                  className="text-xs font-semibold text-brand-600 transition hover:text-brand-700"
                >
                  {showBonusAdvanced ? "Скрыть" : "Показать"} расширенные настройки
                </button>
                {showBonusAdvanced && (
                  <label className="block">
                    <span className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">
                      Срок годности кнопки, дней (после срока нажатие игнорируется)
                    </span>
                    <input
                      type="number"
                      min={1}
                      value={bonusTtlRaw}
                      onChange={(e) => setBonusTtlRaw(e.target.value)}
                      className={`${adminInput} w-24`}
                    />
                  </label>
                )}
                {bonusEnabled && !bonusPayload && (
                  <p className="text-xs text-amber-600 dark:text-amber-400">
                    {bonusType === "days"
                      ? "Заполните дней (3–30) и непустой текст кнопки."
                      : "Укажите код (3–32) и непустой текст кнопки."}
                  </p>
                )}
                {bonusWarnings.length > 0 && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300">
                    <p className="mb-1 font-semibold">Предупреждения по бонусу:</p>
                    <ul className="list-disc space-y-0.5 pl-4">
                      {bonusWarnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
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
            {previewButtons.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {previewButtons.map((b, i) => (
                  <span
                    key={i}
                    className="cursor-default rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-200"
                  >
                    {b.text}
                  </span>
                ))}
              </div>
            )}
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
