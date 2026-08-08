"use client";

import { useCallback, useEffect, useState } from "react";
import { referralApi, type ReferralTxLevel, type ReferralTxType } from "@/entities/referral/api";
import type { ReferralStats, ReferralTransaction } from "@/entities/referral/types";
import { useCopyToClipboard } from "@/shared/hooks/useCopyToClipboard";

function formatRub(n: number): string {
  return n.toFixed(2);
}

/** Conversion % as a rounded string; "—" when nobody invited yet (avoid div-by-zero). */
function conversion(paid: number, invited: number): string {
  if (!invited) return "—";
  return Math.round((paid / invited) * 100) + "%";
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("ru-RU");
  } catch {
    return "";
  }
}

type LevelFilter = "all" | ReferralTxLevel;
type TypeFilter = "all" | ReferralTxType;

const TX_PAGE = 10;

/**
 * Реферальный дашборд в личном кабинете. Верх (баланс, L1/L2, ссылки) тянет
 * GET /api/v1/user/referral; история начислений — отдельный расширенный эндпоинт
 * GET /api/v1/user/referral/transactions с фильтром по уровню/типу и подгрузкой.
 * История показывает только чистые подписи (label) — никаких внутренних
 * description/idempotency_key (раньше там светились данные вебхуков).
 */
export function ReferralCard() {
  const [data, setData] = useState<ReferralStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const { copy, isCopied } = useCopyToClipboard();

  // Extended transaction history (filtered, paginated) — separate from the stats summary.
  const [txs, setTxs] = useState<ReferralTransaction[]>([]);
  const [txLoading, setTxLoading] = useState(false);
  const [txError, setTxError] = useState(false);
  const [levelFilter, setLevelFilter] = useState<LevelFilter>("all");
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [hasMore, setHasMore] = useState(false);

  const fetchTxs = useCallback(
    async (level: LevelFilter, type: TypeFilter, offset: number, append: boolean) => {
      setTxLoading(true);
      setTxError(false);
      try {
        const res = await referralApi.getTransactions({
          level: level === "all" ? undefined : level,
          type: type === "all" ? undefined : type,
          limit: TX_PAGE,
          offset,
        });
        if (!res.ok) {
          setTxError(true);
        } else {
          setTxs((prev) => (append ? [...prev, ...res.data.transactions] : res.data.transactions));
          setHasMore(res.data.has_more);
        }
      } catch {
        setTxError(true);
      } finally {
        setTxLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      setError(false);
      const res = await referralApi.getStats();
      if (!alive) return;
      if (res.ok) {
        setData(res.data.referral);
      } else {
        setError(true);
      }
      setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, []);

  // (Re)load the filtered history whenever a filter changes (reset to first page).
  useEffect(() => {
    void fetchTxs(levelFilter, typeFilter, 0, false);
  }, [levelFilter, typeFilter, fetchTxs]);

  const stats = data?.stats;
  const hasInvited = stats ? stats.level1.invited + stats.level2.invited > 0 : false;

  if (loading) {
    return (
      <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
          Реферальная программа
        </h2>
        <p className="mt-3 text-sm text-gray-400">Загрузка…</p>
      </div>
    );
  }

  if (error || !data || !stats) {
    return (
      <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
          Реферальная программа
        </h2>
        <p className="mt-3 text-sm text-gray-600 dark:text-gray-300">
          Не удалось загрузить данные. Попробуйте обновить страницу.
        </p>
      </div>
    );
  }

  return (
    <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
        Реферальная программа
      </h2>

      {/* Free month per friend — leading offer + counter */}
      <div className="mt-4 rounded-xl bg-gradient-to-br from-amber-50 to-orange-100/60 p-4 dark:from-amber-950/30 dark:to-orange-900/20">
        <p className="text-xs font-semibold text-amber-700 dark:text-amber-400">
          🎁 Бесплатный месяц за друга
        </p>
        <p className="mt-1 text-sm text-gray-700 dark:text-gray-200">
          +30 дней за каждого друга, который оплатит подписку от 1 месяца. Бонус суммируется с кешбеком.
        </p>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Подарено дней за друзей:{" "}
          <span className="font-semibold text-amber-700 dark:text-amber-400">
            {stats.bonus_days_awarded}
          </span>
        </p>
      </div>

      {/* Balance + total earned */}
      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className="rounded-xl bg-gradient-to-br from-brand-50 to-brand-100/50 p-4 text-center dark:from-brand-950/30 dark:to-brand-900/20">
          <p className="text-xs text-brand-600 dark:text-brand-400">Баланс</p>
          <p className="mt-1 text-xl font-bold text-brand-800 dark:text-brand-300">
            {formatRub(data.balance_rubles)} ₽
          </p>
        </div>
        <div className="rounded-xl bg-gradient-to-br from-emerald-50 to-green-100/50 p-4 text-center dark:from-emerald-950/30 dark:to-green-900/20">
          <p className="text-xs text-emerald-600 dark:text-emerald-400">Всего заработано</p>
          <p className="mt-1 text-xl font-bold text-emerald-800 dark:text-emerald-300">
            {formatRub(stats.total_earned_rubles)} ₽
          </p>
        </div>
      </div>

      {/* L1 / L2 stats */}
      <div className="mt-3 grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-gray-200 p-4 dark:border-zinc-600">
          <p className="text-xs font-semibold text-gray-500 dark:text-gray-400">
            1-й уровень (друзья)
          </p>
          <p className="mt-1 text-sm text-gray-700 dark:text-gray-200">
            {stats.level1.invited} приглашено · {stats.level1.paid} оплатило
          </p>
          <p className="mt-1 text-xs text-gray-400">
            конверсия: {conversion(stats.level1.paid, stats.level1.invited)}
          </p>
        </div>
        <div className="rounded-xl border border-gray-200 p-4 dark:border-zinc-600">
          <p className="text-xs font-semibold text-gray-500 dark:text-gray-400">
            2-й уровень (их друзья)
          </p>
          <p className="mt-1 text-sm text-gray-700 dark:text-gray-200">
            {stats.level2.invited} приглашено · {stats.level2.paid} оплатило
          </p>
          <p className="mt-1 text-xs text-gray-400">
            конверсия: {conversion(stats.level2.paid, stats.level2.invited)}
          </p>
        </div>
      </div>

      {/* Share links */}
      <div className="mt-4 rounded-lg bg-gradient-to-br from-emerald-50 to-green-100/50 p-4 dark:from-emerald-950/30 dark:to-green-900/20">
        <p className="text-xs font-medium text-emerald-700 dark:text-emerald-400">
          {!hasInvited
            ? "Отправьте ссылку другу — получите процент с его оплаты"
            : "Продолжайте делиться ссылкой и зарабатывайте с оплат друзей"}
        </p>

        {data.telegram_link && (
          <div className="mt-3">
            <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
              🔗 Для Telegram{" "}
              <span className="font-normal text-gray-400">— друг без сайта</span>
            </p>
            <p className="mt-1 truncate text-xs font-mono text-gray-600 dark:text-gray-400">
              {data.telegram_link}
            </p>
            <button
              onClick={() => copy(data.telegram_link!, "ref-tg")}
              className="mt-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700"
            >
              {isCopied("ref-tg") ? "✓ Скопировано!" : "Копировать"}
            </button>
          </div>
        )}

        <div className="mt-3 border-t border-emerald-200/50 pt-3 dark:border-emerald-800/30">
          <p className="text-xs font-semibold text-gray-700 dark:text-gray-300">
            🌐 Для сайта{" "}
            <span className="font-normal text-gray-400">— друг без Telegram</span>
          </p>
          <p className="mt-1 truncate text-xs font-mono text-gray-600 dark:text-gray-400">
            {data.web_link}
          </p>
          <button
            onClick={() => copy(data.web_link, "ref-web")}
            className="mt-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700"
          >
            {isCopied("ref-web") ? "✓ Скопировано!" : "Копировать"}
          </button>
        </div>
      </div>

      {/* Extended transaction history (clean labels only) */}
      <div className="mt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            История начислений
          </p>
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {(["all", "credit", "debit"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTypeFilter(t)}
                className={`rounded-md px-2 py-1 font-medium ${
                  typeFilter === t
                    ? "bg-brand-600 text-white"
                    : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-zinc-700 dark:text-gray-300"
                }`}
              >
                {t === "all" ? "Все" : t === "credit" ? "Начисления" : "Списания"}
              </button>
            ))}
            {(["all", 1, 2] as const).map((lv) => (
              <button
                key={String(lv)}
                onClick={() => setLevelFilter(lv)}
                className={`rounded-md px-2 py-1 font-medium ${
                  levelFilter === lv
                    ? "bg-brand-600 text-white"
                    : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-zinc-700 dark:text-gray-300"
                }`}
              >
                {lv === "all" ? "Все ур." : `${lv} ур.`}
              </button>
            ))}
          </div>
        </div>

        {txError ? (
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Не удалось загрузить историю. Попробуйте ещё раз.
          </p>
        ) : txs.length === 0 && !txLoading ? (
          <p className="mt-2 text-sm text-gray-400">Пока пусто.</p>
        ) : (
          <ul className="mt-2 divide-y divide-gray-100 dark:divide-zinc-700">
            {txs.map((tx, i) => {
              const isCredit = tx.type === "referral_credit";
              return (
                <li key={i} className="flex items-center justify-between py-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm text-gray-700 dark:text-gray-200">
                      {tx.label}
                      {tx.level ? (
                        <span className="ml-1 text-xs text-gray-400">· {tx.level} ур.</span>
                      ) : null}
                    </p>
                    {formatDate(tx.created_at) && (
                      <p className="text-xs text-gray-400">{formatDate(tx.created_at)}</p>
                    )}
                  </div>
                  <span
                    className={`ml-3 shrink-0 text-sm font-semibold ${
                      isCredit
                        ? "text-emerald-600 dark:text-emerald-400"
                        : "text-gray-500 dark:text-gray-400"
                    }`}
                  >
                    {isCredit ? "+" : "−"}
                    {formatRub(Math.abs(tx.amount_rubles))} ₽
                  </span>
                </li>
              );
            })}
          </ul>
        )}

        {hasMore && !txError && (
          <button
            onClick={() => void fetchTxs(levelFilter, typeFilter, txs.length, true)}
            disabled={txLoading}
            className="mt-2 w-full rounded-lg border border-gray-200 py-2 text-xs font-semibold text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-zinc-600 dark:text-gray-300 dark:hover:bg-zinc-700"
          >
            {txLoading ? "Загрузка…" : "Показать ещё"}
          </button>
        )}
      </div>
    </div>
  );
}
