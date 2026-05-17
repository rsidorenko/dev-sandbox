"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { authApi } from "@/features/auth/api";
import { normalizePlanId } from "@/entities/tariff/helpers";
import { useAuth } from "@/shared/lib/auth";
import { KeysModal } from "@/features/keys/KeysModal";
import { SubscriptionCard } from "@/features/subscription/SubscriptionCard";

export default function DashboardPage() {
  const { loading, authenticated, profile, refresh } = useAuth();
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);
  const [showKeys, setShowKeys] = useState(false);

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

  const sub = profile.subscription;

  return (
    <>
      <section className="py-12">
        <div className="mx-auto max-w-4xl px-4">
          <div className="flex items-center justify-between">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
              Личный кабинет
            </h1>
            <button
              onClick={async () => {
                setLoggingOut(true);
                await authApi.logout();
                await refresh();
                router.push("/");
              }}
              disabled={loggingOut}
              className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              {loggingOut ? "Выход..." : "Выйти"}
            </button>
          </div>

          <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">
              Аккаунт
            </h2>
            <p className="mt-2 text-sm text-gray-900 dark:text-gray-100">
              {profile.user.email}
            </p>
          </div>

          <SubscriptionCard
            sub={sub}
            referral={profile.referral}
            onRefresh={refresh}
            onOpenKeys={() => setShowKeys(true)}
            onRenew={() =>
              router.push(`/payment/${normalizePlanId(sub?.plan_id)}`)
            }
          />

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
