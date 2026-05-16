"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, type KeyEntry } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { siteConfig } from "@/config/site";

const PLANS = [
  { id: "plan_1m", label: "1 месяц", months: 1 },
  { id: "plan_3m", label: "3 месяца", months: 3 },
  { id: "plan_6m", label: "6 месяцев", months: 6 },
];

function planName(planId: string | null): string {
  if (!planId) return "";
  const map: Record<string, string> = {
    "1m": "1 месяц", "3m": "3 месяца", "6m": "6 месяцев",
    plan_1m: "1 месяц", plan_3m: "3 месяца", plan_6m: "6 месяцев",
  };
  return map[planId] || planId;
}

function daysLeft(activeUntil: string): number {
  return Math.max(0, Math.ceil((new Date(activeUntil).getTime() - Date.now()) / 86400000));
}

export default function DashboardPage() {
  const { loading, authenticated, profile, refresh } = useAuth();
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);

  // Keys modal
  const [showKeys, setShowKeys] = useState(false);
  const [keys, setKeys] = useState<KeyEntry[]>([]);
  const [subUrl, setSubUrl] = useState<string | null>(null);
  const [keysLoading, setKeysLoading] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [reissueConfirm, setReissueConfirm] = useState(false);
  const [reissuing, setReissuing] = useState(false);

  // Subscription management
  const [actionLoading, setActionLoading] = useState(false);
  const [showChangePlan, setShowChangePlan] = useState(false);
  const [showChangeDevices, setShowChangeDevices] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState("");
  const [selectedDevices, setSelectedDevices] = useState(5);

  useEffect(() => {
    if (!loading && !authenticated) {
      router.push("/login");
    }
  }, [loading, authenticated, router]);

  useEffect(() => {
    if (profile?.subscription) {
      setSelectedPlan(profile.subscription.plan_id || "plan_1m");
      setSelectedDevices(Math.max(5, profile.subscription.device_count || 5));
    }
  }, [profile]);

  const openKeys = async () => {
    setShowKeys(true);
    setKeysLoading(true);
    const result = await api.user.keys();
    if (result.ok) {
      setKeys(result.data.keys);
      setSubUrl(result.data.subscription_url);
    }
    setKeysLoading(false);
  };

  const handleReissue = async () => {
    setReissuing(true);
    const result = await api.user.reissueKeys();
    if (result.ok) { setKeys(result.data.keys); setSubUrl(result.data.subscription_url); }
    setReissuing(false);
    setReissueConfirm(false);
  };

  if (loading) {
    return (
      <section className="flex min-h-[70vh] items-center justify-center">
        <p className="text-gray-500 dark:text-gray-400">Загрузка...</p>
      </section>
    );
  }

  if (!profile) return null;

  const sub = profile.subscription;

  const normalizePlanId = (id: string | null | undefined): string => {
    if (!id) return "1m";
    return id.replace("plan_", "");
  };

  const handleRenew = () => {
    router.push(`/payment/${normalizePlanId(sub?.plan_id)}`);
  };

  const handleChangePlan = () => {
    router.push(`/payment/${normalizePlanId(selectedPlan)}`);
  };

  const handleChangeDevices = () => {
    router.push(`/payment/${normalizePlanId(sub?.plan_id)}?devices=${selectedDevices}`);
  };

  const handleCancel = async () => {
    setActionLoading(true);
    await api.user.cancelSubscription();
    await refresh();
    setActionLoading(false);
    setShowCancelConfirm(false);
  };

  const copyToClipboard = async (text: string, id: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 2000);
  };
  const isActive = sub?.state === "active";
  const referral = profile.referral;
  const days = sub?.active_until ? daysLeft(sub.active_until) : 0;

  return (
    <>
      <section className="py-12">
        <div className="mx-auto max-w-4xl px-4">
          <div className="flex items-center justify-between">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Личный кабинет</h1>
            <button
              onClick={async () => { setLoggingOut(true); await api.auth.logout(); await refresh(); router.push("/"); }}
              disabled={loggingOut}
              className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 transition hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              {loggingOut ? "Выход..." : "Выйти"}
            </button>
          </div>

          {/* Email */}
          <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">Аккаунт</h2>
            <p className="mt-2 text-sm text-gray-900 dark:text-gray-100">{profile.user.email}</p>
          </div>

          {/* Subscription */}
          <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">Подписка</h2>
            {sub && isActive ? (
              <div className="mt-4 space-y-4">
                {/* Status + info row */}
                <div className="flex items-center gap-2">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-green-500 shadow-sm shadow-green-500/50" />
                  <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">Активна</span>
                  {sub.active_until && (
                    <span className="ml-auto text-xs text-gray-400">до {new Date(sub.active_until).toLocaleDateString("ru-RU")}</span>
                  )}
                </div>

                {/* Stat cards */}
                <div className="grid grid-cols-3 gap-3">
                  <div className="rounded-xl bg-gradient-to-br from-brand-50 to-brand-100/50 p-3 text-center dark:from-brand-950/30 dark:to-brand-900/20">
                    <p className="text-xs text-brand-600 dark:text-brand-400">Тариф</p>
                    <p className="mt-1 text-sm font-bold text-brand-800 dark:text-brand-300">{planName(sub.plan_id)}</p>
                  </div>
                  <div className="rounded-xl bg-gradient-to-br from-green-50 to-emerald-100/50 p-3 text-center dark:from-green-950/30 dark:to-emerald-900/20">
                    <p className="text-xs text-green-600 dark:text-green-400">Осталось</p>
                    <p className="mt-1 text-sm font-bold text-green-800 dark:text-green-300">{days} дн.</p>
                  </div>
                  <div className="rounded-xl bg-gradient-to-br from-violet-50 to-purple-100/50 p-3 text-center dark:from-violet-950/30 dark:to-purple-900/20">
                    <p className="text-xs text-violet-600 dark:text-violet-400">Устройств</p>
                    <p className="mt-1 text-sm font-bold text-violet-800 dark:text-violet-300">{sub.device_count || "—"}</p>
                  </div>
                </div>

                {/* Action grid — 2 columns */}
                <div className="grid grid-cols-2 gap-3">
                  <button
                    onClick={openKeys}
                    className="group flex items-center gap-3 rounded-xl bg-brand-600 px-4 py-3 text-left transition hover:bg-brand-700"
                  >
                    <svg className="h-5 w-5 text-brand-200 transition group-hover:text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" />
                    </svg>
                    <div>
                      <p className="text-sm font-semibold text-white">Показать ключи</p>
                      <p className="text-xs text-brand-200">Настройки и ссылка</p>
                    </div>
                  </button>

                  <button
                    onClick={handleRenew}
                    className="group flex items-center gap-3 rounded-xl border border-brand-200 bg-brand-50 px-4 py-3 text-left transition hover:bg-brand-100 dark:border-brand-800 dark:bg-brand-950/30 dark:hover:bg-brand-900/40"
                  >
                    <svg className="h-5 w-5 text-brand-500 dark:text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.992 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182" />
                    </svg>
                    <div>
                      <p className="text-sm font-semibold text-brand-700 dark:text-brand-300">Продлить</p>
                      <p className="text-xs text-brand-500 dark:text-brand-500">Перейти к оплате</p>
                    </div>
                  </button>

                  <button
                    onClick={() => { setShowChangePlan(true); setShowChangeDevices(false); }}
                    className="group flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-left transition hover:bg-gray-50 dark:border-zinc-600 dark:bg-zinc-800/50 dark:hover:bg-zinc-700/50"
                  >
                    <svg className="h-5 w-5 text-gray-400 dark:text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
                    </svg>
                    <div>
                      <p className="text-sm font-semibold text-gray-700 dark:text-zinc-200">Сменить тариф</p>
                      <p className="text-xs text-gray-400 dark:text-zinc-500">1 / 3 / 6 месяцев</p>
                    </div>
                  </button>

                  <button
                    onClick={() => { setShowChangeDevices(true); setShowChangePlan(false); }}
                    className="group flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-left transition hover:bg-gray-50 dark:border-zinc-600 dark:bg-zinc-800/50 dark:hover:bg-zinc-700/50"
                  >
                    <svg className="h-5 w-5 text-gray-400 dark:text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 1.5H8.25A2.25 2.25 0 006 3.75v16.5a2.25 2.25 0 002.25 2.25h7.5A2.25 2.25 0 0018 20.25V3.75a2.25 2.25 0 00-2.25-2.25H13.5m-3 0V3h3V1.5m-3 0h3m-3 18.75h3" />
                    </svg>
                    <div>
                      <p className="text-sm font-semibold text-gray-700 dark:text-zinc-200">Устройства</p>
                      <p className="text-xs text-gray-400 dark:text-zinc-500">{sub.device_count || 5} шт.</p>
                    </div>
                  </button>
                </div>

                {/* Cancel — subtle bottom link */}
                <div className="text-center">
                  <button
                    onClick={() => setShowCancelConfirm(true)}
                    className="text-xs text-gray-400 transition hover:text-red-500 dark:text-zinc-500 dark:hover:text-red-400"
                  >
                    Отменить подписку
                  </button>
                </div>

                {/* Inline panels */}
                {showChangePlan && (
                  <div className="rounded-xl border border-gray-200 p-4 dark:border-zinc-600">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">Выберите тариф:</p>
                    <div className="flex flex-wrap gap-2">
                      {PLANS.map((p) => (
                        <button
                          key={p.id}
                          onClick={() => setSelectedPlan(p.id)}
                          className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                            selectedPlan === p.id
                              ? "bg-brand-600 text-white shadow-sm"
                              : "border border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
                          }`}
                        >
                          {p.label}
                        </button>
                      ))}
                    </div>
                    <div className="flex gap-2 mt-3">
                      <button onClick={handleChangePlan} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700">
                        Перейти к оплате
                      </button>
                      <button onClick={() => setShowChangePlan(false)} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700">
                        Отмена
                      </button>
                    </div>
                  </div>
                )}

                {showChangeDevices && (
                  <div className="rounded-xl border border-gray-200 p-4 dark:border-zinc-600">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">
                      Количество устройств: <strong className="text-brand-600 dark:text-brand-400">{selectedDevices}</strong>
                    </p>
                    <input
                      type="range" min={5} max={20} value={selectedDevices}
                      onChange={(e) => setSelectedDevices(Number(e.target.value))}
                      className="w-full accent-brand-600"
                    />
                    <div className="flex justify-between text-xs text-gray-400 mt-1"><span>5</span><span>10</span><span>15</span><span>20</span></div>
                    <div className="flex gap-2 mt-3">
                      <button onClick={handleChangeDevices} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700">
                        Перейти к оплате
                      </button>
                      <button onClick={() => setShowChangeDevices(false)} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700">
                        Отмена
                      </button>
                    </div>
                  </div>
                )}

                {showCancelConfirm && (
                  <div className="rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-800/50 dark:bg-red-950/20">
                    <p className="text-sm text-red-600 dark:text-red-400 mb-3">Подписка будет отменена. Доступ сохранится до конца оплаченного периода.</p>
                    <div className="flex gap-2">
                      <button onClick={handleCancel} disabled={actionLoading} className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50">
                        {actionLoading ? "Отмена..." : "Да, отменить"}
                      </button>
                      <button onClick={() => setShowCancelConfirm(false)} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700">
                        Назад
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="mt-3">
                <p className="text-sm text-gray-600 dark:text-gray-300">
                  {sub ? "Подписка отменена или истекла." : "У вас нет активной подписки."}
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

          {/* Referral */}
          {referral && (
            <div className="mt-4 rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-400">Реферальная программа</h2>
              <div className="mt-3 space-y-2">
                <p className="text-sm text-gray-600 dark:text-gray-300">Баланс: <strong>{referral.balance_rubles.toFixed(2)} ₽</strong></p>
                <p className="text-sm text-gray-600 dark:text-gray-300">Рефералов: <strong>{referral.referrals_count}</strong></p>
                <div className="mt-3 rounded-lg bg-gradient-to-br from-amber-50 to-orange-100/50 p-4 dark:from-amber-950/30 dark:to-orange-900/20">
                  <p className="text-xs font-medium text-amber-700 dark:text-amber-400">Пригласите друга и получите бонус</p>
                  <p className="mt-2 truncate text-xs font-mono text-gray-600 dark:text-gray-400">
                    https://t.me/{siteConfig.botUsername}?start=ref_{referral.code}
                  </p>
                  <button
                    onClick={() => copyToClipboard(`https://t.me/${siteConfig.botUsername}?start=ref_${referral.code}`, "ref-link")}
                    className="mt-2 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700"
                  >
                    {copied === "ref-link" ? "Скопировано!" : "Скопировать ссылку"}
                  </button>
                </div>
              </div>
            </div>
          )}

          <div className="mt-6 text-center">
            <Link href="/" className="text-sm font-medium text-brand-600 transition hover:text-brand-700 dark:text-brand-400">&larr; На главную</Link>
          </div>
        </div>
      </section>

      {/* Keys Modal */}
      {showKeys && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-lg max-h-[85vh] overflow-y-auto rounded-2xl bg-white p-6 shadow-xl dark:bg-zinc-800">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">Настройки подключения</h2>
              <button onClick={() => { setShowKeys(false); setReissueConfirm(false); }} className="text-gray-400 transition hover:text-gray-600 dark:hover:text-gray-200">✕</button>
            </div>

            {keysLoading ? (
              <p className="mt-6 text-center text-gray-500 dark:text-gray-400">Загрузка ключей...</p>
            ) : keys.length === 0 ? (
              <p className="mt-6 text-center text-gray-500 dark:text-gray-400">Ключи не найдены</p>
            ) : (
              <>
                {subUrl && (
                  <div className="mt-4 rounded-lg bg-brand-50 p-4 dark:bg-brand-950/30">
                    <p className="text-xs font-medium text-brand-700 dark:text-brand-300">Ссылка для совместимых приложений</p>
                    <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">Все ключи подтянутся автоматически</p>
                    <button onClick={() => copyToClipboard(subUrl, "sub-url")} className="mt-2 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700">
                      {copied === "sub-url" ? "Скопировано!" : "Скопировать ссылку"}
                    </button>
                  </div>
                )}

                <div className="mt-4 space-y-3">
                  {keys.map((k, i) => (
                    <div key={i} className="rounded-lg border border-gray-100 p-3 dark:border-zinc-600">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{k.flag} {k.label}</span>
                        <button onClick={() => copyToClipboard(k.link, `key-${i}`)} className="rounded-lg bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-200 dark:bg-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-600">
                          {copied === `key-${i}` ? "Скопировано!" : "Копировать"}
                        </button>
                      </div>
                      <p className="mt-1 truncate text-xs text-gray-400 font-mono">{k.link}</p>
                    </div>
                  ))}
                </div>

                <div className="mt-4 border-t border-gray-100 pt-4 dark:border-zinc-700">
                  {!reissueConfirm ? (
                    <button onClick={() => setReissueConfirm(true)} disabled={reissuing} className="w-full rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 dark:border-red-800/50 dark:text-red-400 dark:hover:bg-red-900/20">
                      Перевыпустить ключи
                    </button>
                  ) : (
                    <div className="space-y-2">
                      <p className="text-sm text-red-600 dark:text-red-400">Старые ключи перестанут работать. Продолжить?</p>
                      <div className="flex gap-2">
                        <button onClick={handleReissue} disabled={reissuing} className="flex-1 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50">
                          {reissuing ? "Перевыпуск..." : "Да, перевыпустить"}
                        </button>
                        <button onClick={() => setReissueConfirm(false)} className="flex-1 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700">
                          Отмена
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
