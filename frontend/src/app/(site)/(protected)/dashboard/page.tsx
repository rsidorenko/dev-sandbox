"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { clearToken } from "@/shared/lib/tokenStore";
import { normalizePlanId } from "@/entities/tariff/helpers";
import { useAuth } from "@/shared/lib/auth";
import { useCopyToClipboard } from "@/shared/hooks/useCopyToClipboard";
import { useSubscriptionStatus } from "@/features/subscription/useSubscriptionStatus";
import { KeysModal } from "@/features/keys/KeysModal";
import { PromocodeCard } from "@/features/promocode/PromocodeCard";
import { ReferralCard } from "@/features/referral/ReferralCard";
import { SubscriptionCard } from "@/features/subscription/SubscriptionCard";

export default function DashboardPage() {
  const { loading, authenticated, profile } = useAuth();
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);
  const [showKeys, setShowKeys] = useState(false);
  // «Живой» статус: опрос каждые 30с без глобального лоадера (обновляется и
  // сразу после оплаты в другой вкладке/на другом устройстве).
  const live = useSubscriptionStatus(authenticated && profile != null);
  const { isCopied, copy } = useCopyToClipboard();
  const loginUrl = profile?.user.login_url ?? null;

  useEffect(() => {
    if (!loading && !authenticated) router.push("/login");
  }, [loading, authenticated, router]);

  if (loading) {
    return (
      <section className="flex min-h-[70vh] items-center justify-center">
        <p className="text-gray-500 dark:text-gray-400">Загрузка...</p>
      </section>
    );
  }

  if (!profile) return null;

  const sub = live.subscription ?? profile.subscription;

  return (
    <>
      <section className="py-12">
        <div className="mx-auto max-w-4xl px-4">
          <div className="flex items-center justify-between">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
              Личный кабинет
            </h1>
            <button
              onClick={() => {
                setLoggingOut(true);
                // Сессия — токен в памяти: выход = очистка токена (без сервера).
                clearToken();
                router.push("/");
              }}
              disabled={loggingOut}
              className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              {loggingOut ? "Выход..." : "Выйти"}
            </button>
          </div>

          {/* Ссылка — единственный креденшл: пока показываем, даём скопировать.
              Без этого блока первый выход = лок-аут навсегда. */}
          <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
              Ваша ссылка для входа
            </h2>
            {loginUrl ? (
              <>
                <div className="mt-3 flex items-center gap-2">
                  <code className="min-w-0 flex-1 truncate rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-700 dark:bg-zinc-700/60 dark:text-zinc-300">
                    {loginUrl}
                  </code>
                  <button
                    type="button"
                    onClick={() => void copy(loginUrl, "login-url")}
                    className="shrink-0 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-xs font-semibold text-brand-700 transition hover:bg-brand-100 dark:border-brand-800 dark:bg-brand-900/20 dark:text-brand-300"
                  >
                    {isCopied("login-url") ? "Скопировано" : "Копировать"}
                  </button>
                </div>
                <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                  Ссылка — единственный способ входа на сайт. Сохраните её:
                  восстановить потерянную ссылку нельзя (только привязка в
                  Telegram-боте).
                </p>
              </>
            ) : (
              <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                Ссылка для входа недоступна — скопируйте актуальную ссылку
                подписки в Telegram-боте («Моя подписка»).
              </p>
            )}
          </div>

          <SubscriptionCard
            sub={sub}
            referral={profile.referral}
            onRefresh={live.refresh}
            onOpenKeys={() => setShowKeys(true)}
            onRenew={() =>
              router.push(`/payment/${normalizePlanId(sub?.plan_id)}`)
            }
          />

          <ReferralCard />

          <PromocodeCard />

          <div className="mt-6 text-center">
            <Link
              href="/"
              className="text-sm font-medium text-brand-600 transition hover:text-brand-700 dark:text-brand-400"
            >
              &larr; На главную
            </Link>
          </div>
        </div>
      </section>

      <KeysModal open={showKeys} onClose={() => setShowKeys(false)} />
    </>
  );
}
