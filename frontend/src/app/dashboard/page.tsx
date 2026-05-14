"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function DashboardPage() {
  const { loading, authenticated, profile, refresh } = useAuth();
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    if (!loading && !authenticated) {
      router.push("/login");
    }
  }, [loading, authenticated, router]);

  if (loading) {
    return (
      <section className="flex min-h-[70vh] items-center justify-center">
        <p className="text-gray-500">Загрузка...</p>
      </section>
    );
  }

  if (!profile) return null;

  const sub = profile.subscription;
  const isActive = sub?.state === "active";
  const keys = profile.keys;
  const referral = profile.referral;

  return (
    <section className="py-12">
      <div className="mx-auto max-w-4xl px-4">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-900">Личный кабинет</h1>
          <button
            onClick={async () => {
              setLoggingOut(true);
              await api.auth.logout();
              await refresh();
              router.push("/");
            }}
            disabled={loggingOut}
            className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 transition hover:bg-gray-50"
          >
            {loggingOut ? "Выход..." : "Выйти"}
          </button>
        </div>

        {/* Email */}
        <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">Аккаунт</h2>
          <p className="mt-2 text-sm text-gray-900">{profile.user.email}</p>
        </div>

        {/* Subscription */}
        <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">Подписка</h2>
          {sub && isActive ? (
            <div className="mt-3 space-y-2">
              <div className="flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
                <span className="text-sm font-medium text-gray-900">Активна</span>
              </div>
              {sub.active_until && (
                <p className="text-sm text-gray-600">
                  Действует до: {new Date(sub.active_until).toLocaleDateString("ru-RU")}
                </p>
              )}
              {sub.plan_id && <p className="text-sm text-gray-600">Тариф: {_planName(sub.plan_id)}</p>}
              {sub.device_count && (
                <p className="text-sm text-gray-600">Устройств: {sub.device_count}</p>
              )}
              <div className="pt-2">
                <Link
                  href="/payment/renew"
                  className="inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-700"
                >
                  Продлить
                </Link>
              </div>
            </div>
          ) : (
            <div className="mt-3">
              <p className="text-sm text-gray-600">
                {sub ? "Подписка неактивна." : "У вас нет активной подписки."}
              </p>
              <div className="mt-3">
                <Link
                  href="/#tariffs"
                  className="inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-700"
                >
                  Оформить подписку
                </Link>
              </div>
            </div>
          )}
        </div>

        {/* Keys */}
        {isActive && keys && (
          <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
              VPN-ключи
            </h2>
            <div className="mt-3">
              {keys.available ? (
                <p className="text-sm text-gray-600">
                  Ключи доступны. Получите их через Telegram-бота или используйте ссылку подписки.
                </p>
              ) : (
                <p className="text-sm text-gray-500">
                  Ключи готовятся. Попробуйте немного позже.
                </p>
              )}
            </div>
          </div>
        )}

        {/* Referral */}
        {referral && (
          <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
              Реферальная программа
            </h2>
            <div className="mt-3 space-y-2">
              <p className="text-sm text-gray-600">
                Баланс: <strong>{referral.balance_rubles.toFixed(2)} ₽</strong>
              </p>
              <p className="text-sm text-gray-600">
                Рефералов: <strong>{referral.referrals_count}</strong>
              </p>
              <p className="text-sm text-gray-600">
                Код: <code className="rounded bg-gray-100 px-2 py-0.5 text-xs">{referral.code}</code>
              </p>
            </div>
          </div>
        )}

        <div className="mt-6 text-center">
          <Link
            href="/"
            className="text-sm font-medium text-brand-600 transition hover:text-brand-700"
          >
            &larr; На главную
          </Link>
        </div>
      </div>
    </section>
  );
}

function _planName(planId: string): string {
  const names: Record<string, string> = { "1m": "1 месяц", "3m": "3 месяца", "6m": "6 месяцев" };
  return names[planId] || planId;
}
