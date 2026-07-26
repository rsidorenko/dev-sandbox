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
  const byPlan = data?.by_plan ?? [];

  const kpi = data?.kpi;
  const ratePct = kpi?.trial_to_paid_rate == null ? null : Math.round(kpi.trial_to_paid_rate * 100);

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
              label="Конверсия"
              value={ratePct == null ? "—" : `${ratePct}%`}
              sub="триал → платный"
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
            Выручка — только реальные платежи через YooKassa (status=accepted в ledger). Внутренние и
            операторские гранты (grant_subscription, manual_grant) исключены. Историческая выручка до
            внедрения сумм в ledger может быть дополнительно занижена (дыра не перезалита).
          </p>
        </>
      )}
    </div>
  );
}
