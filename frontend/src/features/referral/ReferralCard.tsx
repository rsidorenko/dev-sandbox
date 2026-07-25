"use client";

import { useEffect, useState } from "react";
import { referralApi } from "@/entities/referral/api";
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

function txLabel(tx: ReferralTransaction): string {
  return tx.description || "Реферальное начисление";
}

/**
 * Реферальный дашборд в личном кабинете. Сам тянет GET /api/v1/user/referral
 * (баланс, L1/L2 invited+paid с конверсией, всего заработано, последние начисления)
 * и показывает ссылки для шеринга с копированием. Потребляет rich-эндпоинт
 * (app/web_api/referral.py), отличный от краткого блока в /api/v1/user/profile.
 */
export function ReferralCard() {
  const [data, setData] = useState<ReferralStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const { copy, isCopied } = useCopyToClipboard();

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

  if (error || !data) {
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

  const s = data.stats;
  const hasInvited = s.level1.invited + s.level2.invited > 0;
  const txs = data.recent_transactions ?? [];

  return (
    <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
        Реферальная программа
      </h2>

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
            {formatRub(s.total_earned_rubles)} ₽
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
            {s.level1.invited} приглашено · {s.level1.paid} оплатило
          </p>
          <p className="mt-1 text-xs text-gray-400">
            конверсия: {conversion(s.level1.paid, s.level1.invited)}
          </p>
        </div>
        <div className="rounded-xl border border-gray-200 p-4 dark:border-zinc-600">
          <p className="text-xs font-semibold text-gray-500 dark:text-gray-400">
            2-й уровень (их друзья)
          </p>
          <p className="mt-1 text-sm text-gray-700 dark:text-gray-200">
            {s.level2.invited} приглашено · {s.level2.paid} оплатило
          </p>
          <p className="mt-1 text-xs text-gray-400">
            конверсия: {conversion(s.level2.paid, s.level2.invited)}
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

      {/* Recent credits */}
      {txs.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            Последние начисления
          </p>
          <ul className="mt-2 divide-y divide-gray-100 dark:divide-zinc-700">
            {txs.map((tx, i) => (
              <li key={i} className="flex items-center justify-between py-2">
                <div className="min-w-0">
                  <p className="truncate text-sm text-gray-700 dark:text-gray-200">
                    {txLabel(tx)}
                  </p>
                  {formatDate(tx.created_at) && (
                    <p className="text-xs text-gray-400">
                      {formatDate(tx.created_at)}
                    </p>
                  )}
                </div>
                <span className="ml-3 shrink-0 text-sm font-semibold text-emerald-600 dark:text-emerald-400">
                  +{formatRub(tx.amount_rubles)} ₽
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
