"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { adminApi } from "@/features/admin/api";
import { adminBtnPrimary, adminBtnSecondary, adminNum } from "@/features/admin/ui";
import type {
  AdminStatsPeriod,
  AdminStatsResponse,
  AdminStatsCountPoint,
  AdminStatsRevenuePoint,
  AdminStatsTrafficPoint,
} from "@/entities/admin/types";
import { Alert } from "@/shared/ui/Alert";

// Один акцентный цвет на весь дашборд (одноразмерные графики — категориальная палитра не нужна).
const ACCENT = "#2563eb";

const PERIODS: { key: AdminStatsPeriod; label: string }[] = [
  { key: "1d", label: "1 день" },
  { key: "7d", label: "7 дней" },
  { key: "30d", label: "30 дней" },
  { key: "90d", label: "90 дней" },
  { key: "all", label: "Всё время" },
];

const compactRub = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 });

function useIsDark(): boolean {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const el = document.documentElement;
    const update = () => setDark(el.classList.contains("dark"));
    update();
    const obs = new MutationObserver(update);
    obs.observe(el, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

function formatBucket(value: unknown, bucket: "hour" | "day" | "month"): string {
  const d = new Date(String(value));
  if (Number.isNaN(d.getTime())) return String(value);
  if (bucket === "hour") return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (bucket === "month") return d.toLocaleDateString("ru-RU", { month: "short", year: "2-digit" });
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

function formatBucketFull(value: unknown, bucket: "hour" | "day" | "month"): string {
  const d = new Date(String(value));
  if (Number.isNaN(d.getTime())) return String(value);
  if (bucket === "hour") return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  if (bucket === "month") return d.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "long", year: "numeric" });
}

function formatRubles(n: number): string {
  return `${n.toLocaleString("ru-RU", { maximumFractionDigits: 0 })} ₽`;
}

function formatRublesNullable(n: number | null): string {
  return n == null ? "—" : `${n.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

function formatRoas(n: number | null): string {
  return n == null ? "—" : `${n.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}×`;
}

function todayInMoscow(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

function formatGb(n: number): string {
  return `${n.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ГБ`;
}

function ChartCard({ title, hint, isEmpty, children }: { title: string; hint?: string; isEmpty?: boolean; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{title}</h2>
        {hint && <span className="text-xs text-gray-400 dark:text-gray-500">{hint}</span>}
      </div>
      <div className="relative h-[280px]">
        {isEmpty ? (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">
            Нет данных за период
          </div>
        ) : (
          children
        )}
      </div>
    </section>
  );
}

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
      <div className="text-xs font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500">{label}</div>
      <div className={`mt-2 text-2xl font-bold text-gray-900 dark:text-gray-100 ${adminNum}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-gray-400 dark:text-gray-500">{sub}</div>}
    </div>
  );
}

export default function AdminStatsPage() {
  const [period, setPeriod] = useState<AdminStatsPeriod>("7d");
  const [data, setData] = useState<AdminStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [spendCampaign, setSpendCampaign] = useState("");
  const [spendDate, setSpendDate] = useState(todayInMoscow);
  const [spendAmount, setSpendAmount] = useState("");
  const [spendSaving, setSpendSaving] = useState(false);
  const [spendMessage, setSpendMessage] = useState("");
  const [spendError, setSpendError] = useState("");

  const load = useCallback(async (p: AdminStatsPeriod) => {
    setLoading(true);
    setError("");
    const r = await adminApi.stats(p);
    if (r.ok) {
      setData(r.data);
    } else if (r.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent("/admin/stats")}`;
      return;
    } else {
      setError("Не удалось загрузить статистику.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load(period);
  }, [period, load]);

  const dark = useIsDark();
  const theme = useMemo(
    () => ({
      grid: dark ? "#27272a" : "#e5e7eb",
      axis: dark ? "#71717a" : "#6b7280",
      tooltip: dark
        ? { background: "#18181b", border: "1px solid #3f3f46", borderRadius: 12, color: "#e4e4e7", fontSize: 12 }
        : { background: "#ffffff", border: "1px solid #e5e7eb", borderRadius: 12, color: "#374151", fontSize: 12 },
    }),
    [dark],
  );

  const bucket = data?.period.bucket ?? "day";

  const revData: AdminStatsRevenuePoint[] = data?.series.revenue ?? [];
  const newUsersData: AdminStatsCountPoint[] = data?.series.new_users ?? [];
  const trialsData: AdminStatsCountPoint[] = data?.series.trials ?? [];
  const trafficData: AdminStatsTrafficPoint[] = data?.series.traffic ?? [];
  const trafficByServer = data?.traffic_by_server ?? [];
  const byPlan = data?.by_plan ?? [];
  const telegramAds = data?.telegram_ads?.campaigns ?? [];
  const economics = data?.telegram_ads?.economics;

  const saveSpend = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSpendSaving(true);
    setSpendError("");
    setSpendMessage("");
    const result = await adminApi.setTelegramAdsSpend({
      campaign_key: spendCampaign,
      spend_date: spendDate,
      amount_rubles: spendAmount,
    });
    if (result.ok) {
      setSpendCampaign(result.data.spend.campaign_key);
      setSpendAmount("");
      setSpendMessage(result.data.spend.changed ? "Расход сохранён." : "Расход уже был таким — изменений нет.");
      await load(period);
    } else if (result.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent("/admin/stats")}`;
      return;
    } else if (result.error === "csrf_validation_failed") {
      setSpendError("Сессия устарела. Обновите страницу и повторите.");
    } else {
      setSpendError("Не удалось сохранить расход. Проверьте кампанию, дату и сумму.");
    }
    setSpendSaving(false);
  };

  const kpi = data?.kpi;
  const funnel = data?.funnel;
  const ratePct = funnel?.trial_to_first_payment_rate == null
    ? null : Math.round(funnel.trial_to_first_payment_rate * 1000) / 10;

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Статистика</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {data ? `Период: ${formatBucketFull(data.period.from, "day")} — сегодня` : "Загрузка…"}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {PERIODS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => setPeriod(p.key)}
              className={period === p.key ? adminBtnPrimary : adminBtnSecondary}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {error && <Alert className="mb-4">{error}</Alert>}

      {loading && !data ? (
        <div className="flex h-64 items-center justify-center text-gray-400 dark:text-gray-500">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
          <span className="ml-3 text-sm">Загрузка…</span>
        </div>
      ) : (
        <>
          {/* KPI */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            <KpiCard label="Выручка" value={formatRubles(kpi?.revenue_rubles ?? 0)} sub={`${kpi?.payments_count ?? 0} платежей`} />
            <KpiCard label="Платящие" value={(kpi?.paying_users ?? 0).toLocaleString("ru-RU")} sub="пользователей" />
            <KpiCard label="Новые" value={(kpi?.new_users ?? 0).toLocaleString("ru-RU")} sub="регистраций" />
            <KpiCard label="Триалы" value={(kpi?.trials_started ?? 0).toLocaleString("ru-RU")} sub="активировано" />
            <KpiCard label="Активных" value={(kpi?.active_subscriptions ?? 0).toLocaleString("ru-RU")} sub="подписок сейчас" />
            <KpiCard
              label="Триал → первая оплата"
              value={ratePct == null ? "—" : `${ratePct}%`}
              sub={funnel
                ? `${funnel.converted_trials} из ${funnel.mature_trials} зрелых · ожидают ${funnel.pending_trials} · окно ${funnel.window_days} дней`
                : "Когортные данные недоступны"}
            />
            <KpiCard
              label="Трафик"
              value={formatGb(kpi?.traffic_gb ?? 0)}
              sub={`до ${kpi?.traffic_users ?? 0} польз./сутки`}
            />
          </div>

          {/* График выручки */}
          <div className="mt-6">
            <ChartCard title="Выручка" hint="по дням" isEmpty={revData.length === 0}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={revData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="revFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={ACCENT} stopOpacity={0.25} />
                      <stop offset="95%" stopColor={ACCENT} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.grid} vertical={false} />
                  <XAxis
                    dataKey="bucket"
                    stroke={theme.axis}
                    tick={{ fontSize: 12 }}
                    minTickGap={24}
                    tickFormatter={(v) => formatBucket(v, bucket)}
                  />
                  <YAxis
                    stroke={theme.axis}
                    tick={{ fontSize: 12 }}
                    width={56}
                    tickFormatter={(v) => compactRub.format(Number(v))}
                  />
                  <Tooltip
                    contentStyle={theme.tooltip}
                    labelFormatter={(v) => formatBucketFull(v, bucket)}
                    formatter={(value) => [formatRubles(Number(value)), "Выручка"]}
                  />
                  <Area
                    type="monotone"
                    dataKey="rubles"
                    stroke={ACCENT}
                    strokeWidth={2}
                    fill="url(#revFill)"
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          {/* Регистрации + триалы */}
          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <ChartCard title="Новые пользователи" isEmpty={newUsersData.length === 0}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={newUsersData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.grid} vertical={false} />
                  <XAxis
                    dataKey="bucket"
                    stroke={theme.axis}
                    tick={{ fontSize: 12 }}
                    minTickGap={24}
                    tickFormatter={(v) => formatBucket(v, bucket)}
                  />
                  <YAxis stroke={theme.axis} tick={{ fontSize: 12 }} width={32} allowDecimals={false} />
                  <Tooltip
                    contentStyle={theme.tooltip}
                    labelFormatter={(v) => formatBucketFull(v, bucket)}
                    formatter={(value) => [Number(value).toLocaleString("ru-RU"), "Новых"]}
                  />
                  <Bar dataKey="count" fill={ACCENT} radius={[4, 4, 0, 0]} maxBarSize={28} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>

            <ChartCard title="Активация триалов" isEmpty={trialsData.length === 0}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={trialsData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.grid} vertical={false} />
                  <XAxis
                    dataKey="bucket"
                    stroke={theme.axis}
                    tick={{ fontSize: 12 }}
                    minTickGap={24}
                    tickFormatter={(v) => formatBucket(v, bucket)}
                  />
                  <YAxis stroke={theme.axis} tick={{ fontSize: 12 }} width={32} allowDecimals={false} />
                  <Tooltip
                    contentStyle={theme.tooltip}
                    labelFormatter={(v) => formatBucketFull(v, bucket)}
                    formatter={(value) => [Number(value).toLocaleString("ru-RU"), "Триалов"]}
                  />
                  <Bar dataKey="count" fill={ACCENT} radius={[4, 4, 0, 0]} maxBarSize={28} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          <section className="mt-6 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
            <div className="flex flex-col gap-1 border-b border-gray-200 px-5 py-4 dark:border-zinc-700 sm:flex-row sm:items-baseline sm:justify-between">
              <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Экономика Telegram Ads</h2>
              <span className="text-xs text-gray-400 dark:text-gray-500">
                first-touch · метки ads_* и tgads_* · ручной расход в ₽
              </span>
            </div>
            <form onSubmit={saveSpend} className="border-b border-gray-200 px-5 py-4 dark:border-zinc-700">
              <div className="grid gap-3 sm:grid-cols-[minmax(180px,1fr)_170px_170px_auto] sm:items-end">
                <label className="text-xs font-medium text-gray-600 dark:text-gray-300">
                  Кампания
                  <input
                    value={spendCampaign}
                    onChange={(event) => setSpendCampaign(event.target.value)}
                    list="telegram-ads-campaigns"
                    placeholder="apk_01 или ads_apk_01"
                    required
                    className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-brand-500 dark:border-zinc-600 dark:bg-zinc-900 dark:text-gray-100"
                  />
                  <datalist id="telegram-ads-campaigns">
                    {telegramAds.map((campaign) => <option key={campaign.campaign_key} value={campaign.campaign_key} />)}
                  </datalist>
                </label>
                <label className="text-xs font-medium text-gray-600 dark:text-gray-300">
                  Дата расхода
                  <input
                    type="date"
                    min="2025-01-01"
                    max={todayInMoscow()}
                    value={spendDate}
                    onChange={(event) => setSpendDate(event.target.value)}
                    required
                    className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-brand-500 dark:border-zinc-600 dark:bg-zinc-900 dark:text-gray-100"
                  />
                </label>
                <label className="text-xs font-medium text-gray-600 dark:text-gray-300">
                  Расход, ₽
                  <input
                    inputMode="decimal"
                    value={spendAmount}
                    onChange={(event) => setSpendAmount(event.target.value)}
                    placeholder="1000.00"
                    required
                    className="mt-1 block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-brand-500 dark:border-zinc-600 dark:bg-zinc-900 dark:text-gray-100"
                  />
                </label>
                <button type="submit" disabled={spendSaving} className={adminBtnPrimary}>
                  {spendSaving ? "Сохраняем…" : "Сохранить"}
                </button>
              </div>
              <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
                Сумма заменяет расход этой кампании за выбранный день. Введите 0, чтобы удалить запись.
              </p>
              {spendError && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{spendError}</p>}
              {spendMessage && <p className="mt-2 text-xs text-emerald-600 dark:text-emerald-400">{spendMessage}</p>}
            </form>
            <div className="grid grid-cols-2 gap-3 border-b border-gray-200 p-5 dark:border-zinc-700 sm:grid-cols-3 lg:grid-cols-6">
              <KpiCard label="Расход" value={formatRubles(economics?.spend_rubles ?? 0)} sub="за выбранный период" />
              <KpiCard label="Клиенты" value={(economics?.acquired_users ?? 0).toLocaleString("ru-RU")} sub="первая оплата ≤ 10 дней" />
              <KpiCard label="CAC" value={formatRublesNullable(economics?.cac_rubles ?? null)} sub="расход / клиенты" />
              <KpiCard label="Рекламная выручка" value={formatRubles(economics?.revenue_rubles ?? 0)} sub="с продлениями" />
              <KpiCard label="ROAS" value={formatRoas(economics?.roas ?? null)} sub="выручка / расход" />
              <KpiCard label="Цена триала" value={formatRublesNullable(economics?.cost_per_trial_rubles ?? null)} sub="расход / триалы" />
            </div>
            {telegramAds.length === 0 ? (
              <div className="px-5 py-10 text-center text-sm text-gray-400 dark:text-gray-500">
                Нет атрибуций или расходов за период
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-[1820px] w-full text-left text-xs">
                  <thead className="bg-gray-50 text-gray-500 dark:bg-zinc-900/50 dark:text-gray-400">
                    <tr>
                      {[
                        "Кампания", "Расход", "Пользователи", "Цена польз.", "Триалы", "Цена триала",
                        "Настройка", "Подключились", "Checkout", "Счета", "Клиенты", "CAC",
                        "Выручка", "ROAS", "Зрелая 1-я оплата", "2-я оплата", "Триал → оплата",
                      ].map((label) => <th key={label} className="whitespace-nowrap px-4 py-3 font-medium">{label}</th>)}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 dark:divide-zinc-700">
                    {telegramAds.map((campaign) => {
                      const paidRate = campaign.trial_to_first_payment_rate == null
                        ? "—" : `${Math.round(campaign.trial_to_first_payment_rate * 1000) / 10}%`;
                      return (
                        <tr key={campaign.campaign_key} className="text-gray-700 dark:text-gray-300">
                          <td className="whitespace-nowrap px-4 py-3 font-mono font-medium text-gray-900 dark:text-gray-100">
                            {campaign.campaign_key}
                          </td>
                          <td className={`px-4 py-3 ${adminNum}`}>{formatRubles(campaign.spend_rubles)}</td>
                          <td className={`px-4 py-3 ${adminNum}`}>{campaign.attributed_users}</td>
                          <td className={`px-4 py-3 ${adminNum}`}>{formatRublesNullable(campaign.cost_per_attributed_user_rubles)}</td>
                          <td className={`px-4 py-3 ${adminNum}`}>
                            {campaign.trials_started}
                            <span className="ml-1 text-gray-400">
                              ({Math.round((campaign.trial_activation_rate ?? 0) * 1000) / 10}%)
                            </span>
                          </td>
                          <td className={`px-4 py-3 ${adminNum}`}>{formatRublesNullable(campaign.cost_per_trial_rubles)}</td>
                          <td className={`px-4 py-3 ${adminNum}`}>{campaign.setup_started_users}</td>
                          <td className={`px-4 py-3 ${adminNum}`}>{campaign.connection_confirmed_users}</td>
                          <td className={`px-4 py-3 ${adminNum}`}>{campaign.checkout_opened_users}</td>
                          <td className={`px-4 py-3 ${adminNum}`}>{campaign.payment_created_users}</td>
                          <td className={`px-4 py-3 font-medium ${adminNum}`}>{campaign.acquired_users}</td>
                          <td className={`px-4 py-3 font-medium ${adminNum}`}>{formatRublesNullable(campaign.cac_rubles)}</td>
                          <td className={`px-4 py-3 ${adminNum}`}>{formatRubles(campaign.revenue_rubles)}</td>
                          <td className={`px-4 py-3 font-medium ${adminNum}`}>{formatRoas(campaign.roas)}</td>
                          <td className={`px-4 py-3 ${adminNum}`}>
                            {campaign.first_payment_users}/{campaign.mature_trials}
                            {campaign.pending_trials > 0 && <span className="ml-1 text-gray-400">+{campaign.pending_trials} ждут</span>}
                          </td>
                          <td className={`px-4 py-3 ${adminNum}`}>{campaign.second_payment_users}</td>
                          <td className={`px-4 py-3 font-medium ${adminNum}`}>{paidRate}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {data?.telegram_ads?.truncated && (
              <p className="border-t border-gray-200 px-5 py-3 text-xs text-amber-600 dark:border-zinc-700 dark:text-amber-400">
                Показаны первые 200 из {data.telegram_ads.total_campaigns} кампаний.
              </p>
            )}
          </section>

          {/* Трафик bare_xray: серия всегда дневная (stat_date — DATE в MSK),
              независимо от period.bucket остальных графиков */}
          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <ChartCard title="Трафик" hint="ГБ/день · up+down" isEmpty={trafficData.length === 0}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trafficData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="trafficFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={ACCENT} stopOpacity={0.25} />
                      <stop offset="95%" stopColor={ACCENT} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.grid} vertical={false} />
                  <XAxis
                    dataKey="bucket"
                    stroke={theme.axis}
                    tick={{ fontSize: 12 }}
                    minTickGap={24}
                    tickFormatter={(v) => formatBucket(v, "day")}
                  />
                  <YAxis
                    stroke={theme.axis}
                    tick={{ fontSize: 12 }}
                    width={56}
                    tickFormatter={(v) => `${Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}`}
                  />
                  <Tooltip
                    contentStyle={theme.tooltip}
                    labelFormatter={(v) => formatBucketFull(v, "day")}
                    formatter={(value, name) =>
                      name === "users"
                        ? [Number(value).toLocaleString("ru-RU"), "Пользователей"]
                        : [formatGb(Number(value)), "Трафик"]
                    }
                  />
                  <Area
                    type="monotone"
                    dataKey="gb"
                    stroke={ACCENT}
                    strokeWidth={2}
                    fill="url(#trafficFill)"
                    dot={{ r: 2 }}
                    activeDot={{ r: 4 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </ChartCard>

            <ChartCard title="Трафик по серверам" hint="ГБ за период" isEmpty={trafficByServer.length === 0}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={trafficByServer} layout="vertical" margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.grid} horizontal={false} />
                  <XAxis
                    type="number"
                    stroke={theme.axis}
                    tick={{ fontSize: 12 }}
                    tickFormatter={(v) => `${Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 1 })}`}
                  />
                  <YAxis type="category" dataKey="label" stroke={theme.axis} tick={{ fontSize: 12 }} width={110} />
                  <Tooltip
                    contentStyle={theme.tooltip}
                    formatter={(value, name) =>
                      name === "users"
                        ? [Number(value).toLocaleString("ru-RU"), "Пользователей"]
                        : [formatGb(Number(value)), "Трафик"]
                    }
                  />
                  <Bar dataKey="gb" fill={ACCENT} radius={[0, 4, 4, 0]} maxBarSize={24} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          {/* Активные подписки по тарифам */}
          <div className="mt-6">
            <ChartCard title="Активные подписки по тарифам" hint="сейчас" isEmpty={byPlan.length === 0}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={byPlan} layout="vertical" margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={theme.grid} horizontal={false} />
                  <XAxis type="number" stroke={theme.axis} tick={{ fontSize: 12 }} allowDecimals={false} />
                  <YAxis
                    type="category"
                    dataKey="plan_id"
                    stroke={theme.axis}
                    tick={{ fontSize: 12 }}
                    width={56}
                  />
                  <Tooltip
                    contentStyle={theme.tooltip}
                    formatter={(value) => [Number(value).toLocaleString("ru-RU"), "Подписок"]}
                  />
                  <Bar dataKey="count" fill={ACCENT} radius={[0, 4, 4, 0]} maxBarSize={24} />
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>

          <p className="mt-4 text-xs text-gray-400 dark:text-gray-500">
            Выручка — принятые платёжные факты в ledger. Помеченные внутренние аккаунты и
            операторские гранты исключены. Конверсия — первая внешняя оплата подписки в RUB
            за 10 дней от старта триала; незрелые триалы не входят в знаменатель. Историческая выручка до
            внедрения сумм в ledger может быть дополнительно занижена (дыра не перезалита). Трафик —
            bare_xray (сбор с 07.09.2026): up+down по всем покрытым серверам, день = MSK.
            Telegram Ads — первый распознанный рекламный переход пользователя, не клики и не показы;
            подтверждение подключения основано на ответе пользователя.
          </p>
        </>
      )}
    </div>
  );
}
