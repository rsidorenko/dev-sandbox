"use client";

import { useEffect, useRef, useState } from "react";
import { CLAIM_ERROR_MESSAGES, type WebNotification } from "./api";
import { sanitizeHtml } from "./sanitizeHtml";
import { useNotifications } from "./useNotifications";

/** Колокольчик в хедере: бейдж непрочитанных + dropdown-лента (polling 45с в хуке). */

const KIND_BADGES: Record<string, string> = {
  broadcast: "Рассылка",
  trial_expiring: "Триал",
  trial_expired: "Триал",
  subscription_expiring: "Подписка",
  subscription_expired: "Подписка",
  key_deleted: "Ключи",
  referral_offer: "Рефералы",
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function NotificationBody({ item }: { item: WebNotification }) {
  if (!item.body) return null;
  if (item.kind === "broadcast") {
    return (
      <div
        className="mt-1 break-words text-sm text-gray-600 dark:text-zinc-300 [&_a]:text-brand-600 [&_a]:underline dark:[&_a]:text-brand-400"
        dangerouslySetInnerHTML={{ __html: sanitizeHtml(item.body) }}
      />
    );
  }
  return (
    <p className="mt-1 whitespace-pre-line break-words text-sm text-gray-600 dark:text-zinc-300">
      {item.body}
    </p>
  );
}

function ClaimButton({
  item,
  claiming,
  onClaim,
}: {
  item: WebNotification;
  claiming: boolean;
  onClaim: (id: number) => void;
}) {
  const bonus = item.payload?.bonus;
  if (!bonus) return null;

  if (item.claim_status === "claimed") {
    return (
      <span className="mt-2 inline-flex items-center rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-500 dark:bg-zinc-800 dark:text-zinc-400">
        Получено
      </span>
    );
  }
  if (item.claim_status === "expired") {
    return (
      <span className="mt-2 inline-flex items-center rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-400 dark:bg-zinc-800 dark:text-zinc-500">
        Срок истёк
      </span>
    );
  }
  if (item.claim_status !== "eligible") return null;

  return (
    <button
      onClick={() => onClaim(item.id)}
      disabled={claiming}
      className="mt-2 inline-flex items-center rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {claiming ? "Получаем…" : bonus.label || "Получить бонус"}
    </button>
  );
}

export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const [claimError, setClaimError] = useState<string | null>(null);
  const [channelSubscriptionRequired, setChannelSubscriptionRequired] = useState(false);
  const {
    items,
    unreadCount,
    hasMore,
    loadingMore,
    claimingId,
    refresh,
    loadMore,
    markAllRead,
    claim,
  } = useNotifications(true);
  const rootRef = useRef<HTMLDivElement>(null);

  // Закрытие по клику вне и по Escape
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // Открытие панели = пользователь увидел список → помечаем прочитанными.
  useEffect(() => {
    if (open) void markAllRead();
    // markAllRead стабилен (useCallback без зависимостей)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleClaim = async (id: number) => {
    setClaimError(null);
    setChannelSubscriptionRequired(false);
    const outcome = await claim(id);
    if (!outcome.ok) {
      setClaimError(CLAIM_ERROR_MESSAGES[outcome.error] || "Не удалось получить бонус.");
      setChannelSubscriptionRequired(outcome.error === "channel_subscription_required");
    }
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        onClick={() => {
          setOpen((v) => !v);
          void refresh();
        }}
        className="relative rounded-lg p-2 text-gray-500 transition hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-zinc-800"
        aria-label={`Уведомления${unreadCount > 0 ? ` (${unreadCount} непрочитанных)` : ""}`}
      >
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-5 w-5">
          <path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold leading-none text-white">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-96 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
          <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3 dark:border-zinc-700">
            <span className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Уведомления</span>
            <button
              onClick={() => void markAllRead()}
              className="text-xs font-medium text-gray-500 transition hover:text-brand-600 dark:text-zinc-400 dark:hover:text-brand-400"
            >
              Прочитать все
            </button>
          </div>

          <div className="max-h-[26rem] overflow-y-auto">
            {items.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-gray-400 dark:text-zinc-500">
                Пока нет уведомлений
              </p>
            ) : (
              items.map((item) => (
                <div
                  key={item.id}
                  className={`border-b border-gray-50 px-4 py-3 last:border-b-0 dark:border-zinc-800 ${
                    item.read_at ? "" : "bg-brand-50/60 dark:bg-zinc-800/60"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      {!item.read_at && (
                        <span className="h-2 w-2 shrink-0 rounded-full bg-brand-500" aria-hidden />
                      )}
                      <span className="text-sm font-semibold text-gray-900 dark:text-zinc-100">
                        {item.title}
                      </span>
                    </div>
                    <span className="shrink-0 text-[11px] text-gray-400 dark:text-zinc-500">
                      {formatDate(item.created_at)}
                    </span>
                  </div>
                  <span className="mt-0.5 inline-block rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-gray-500 dark:bg-zinc-800 dark:text-zinc-400">
                    {KIND_BADGES[item.kind] || item.kind}
                  </span>
                  <NotificationBody item={item} />
                  <ClaimButton item={item} claiming={claimingId === item.id} onClaim={handleClaim} />
                </div>
              ))
            )}
            {hasMore && (
              <button
                onClick={() => void loadMore()}
                disabled={loadingMore}
                className="w-full px-4 py-3 text-center text-xs font-medium text-brand-600 transition hover:bg-gray-50 disabled:opacity-60 dark:text-brand-400 dark:hover:bg-zinc-800"
              >
                {loadingMore ? "Загружаем…" : "Показать ещё"}
              </button>
            )}
          </div>

          {claimError && (
            <div className="border-t border-gray-100 px-4 py-2 text-xs text-red-600 dark:border-zinc-700 dark:text-red-400">
              {claimError}
              {channelSubscriptionRequired && (
                <>
                  {" "}
                  <a
                    href="https://t.me/bravada_vpn"
                    target="_blank"
                    rel="noreferrer"
                    className="font-semibold underline"
                  >
                    Подписаться на канал
                  </a>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
