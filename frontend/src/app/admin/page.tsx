"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAdminRead } from "@/features/admin/useAdminRead";
import { adminDate } from "@/features/admin/display";
import { adminBtnSecondary, adminInput } from "@/features/admin/ui";
import type { AdminOverviewResponse } from "@/entities/admin/types";

const panel = "rounded-2xl border border-gray-200 bg-white p-5 dark:border-zinc-700 dark:bg-zinc-800";
function Metric({ title, value, hint, href }: { title: string; value: string | number; hint: string; href: string }) {
  return <Link href={href} className={`${panel} block transition hover:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-500`}><h2 className="text-sm text-gray-500 dark:text-gray-400">{title}</h2><p className="mt-3 text-3xl font-bold tabular-nums">{value}</p><p className="mt-2 text-xs text-gray-500 dark:text-gray-400">{hint} →</p></Link>;
}
export default function AdminOverviewPage() {
  const { data, loading, error, refresh } = useAdminRead<AdminOverviewResponse>("/api/v1/admin/overview");
  const [search, setSearch] = useState("");
  const router = useRouter();
  return <div className="space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="mb-1 text-xs font-semibold uppercase tracking-wider text-brand-600 dark:text-brand-400">Bravada VPN</p><h1 className="text-2xl font-bold">Обзор сервиса</h1><p className="mt-2 text-sm text-gray-500 dark:text-gray-400">Пользователи, оплаты и задачи поддержки в одном месте.</p></div><button className={adminBtnSecondary} onClick={() => void refresh()} disabled={loading}>Обновить обзор</button></div>
    <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); router.push(`/admin/users?search=${encodeURIComponent(search.trim())}`); }}><input className={`${adminInput} min-w-0 flex-1`} aria-label="Найти пользователя" value={search} onChange={e => setSearch(e.target.value)} placeholder="Telegram ID или внутренний ID" /><button className={adminBtnSecondary}>Открыть поиск</button></form>
    {error && <p role="alert" className="text-red-600">{error}</p>}{loading && <p role="status">Загрузка…</p>}
    {!loading && data && <>
      <p className="text-xs text-gray-500 dark:text-gray-400">Источник: согласованный снимок БД · получено {adminDate(data.observed_at)} МСК. Показатели «сегодня» — с начала суток в Москве.</p>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Metric title="Активный доступ" value={data.active_users} hint="Триал или подписка, включая тестовые аккаунты" href="/admin/users?status=active" />
        <Metric title="Выручка сегодня, RUB" value={`${(data.revenue_kopecks / 100).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`} hint={`${data.payments} фактов оплаты${data.unknown_amount_payments ? ` · без суммы: ${data.unknown_amount_payments}` : ""}; исключены тестовые и операторские`} href="/admin/stats?section=sales&period=1d" />
        <Metric title="Новые пользователи сегодня" value={data.new_users} hint="Без внутренних и накрученных аккаунтов" href="/admin/stats?section=product&period=1d" />
        <Metric title="Инциденты в работе" value={data.open_incidents} hint="Открытые и взятые в работу" href="/admin/incidents" />
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">Выручка обзора учитывает принятые оплаты подписок и устройств в RUB с неотрицательной суммой. Общая статистика включает и другие валюты и типы финансовых фактов, поэтому её сумма может отличаться.</p>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <section className={panel}><div className="flex flex-wrap justify-between gap-3"><h2 className="text-lg font-semibold">Требует внимания · {data.attention_total}</h2><Link href="/admin/incidents" className="text-sm text-brand-600 dark:text-brand-400 underline">Все сигналы и инциденты</Link></div>
          {data.issues.length ? <ul className="mt-4 divide-y dark:divide-zinc-700">{data.issues.map(issue => <li key={issue.key} className="py-3"><p className={issue.severity === "critical" ? "text-red-600" : "text-amber-700 dark:text-amber-400"}>{issue.title}</p><Link className="mt-1 inline-block text-sm text-brand-600 dark:text-brand-400 underline" href={issue.internal_user_id ? `/admin/users/${encodeURIComponent(issue.internal_user_id)}` : issue.kind === "server" ? "/admin/servers" : "/admin/incidents"}>Разобраться →</Link></li>)}</ul> : <p className="mt-4 text-sm text-gray-500 dark:text-gray-400">По проверяемым условиям активных сигналов нет. Неизвестные наблюдения серверов показаны отдельно.</p>}
          {data.attention_total > data.issues.length && <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">Показаны первые {data.issues.length} сигналов из {data.attention_total}.</p>}
        </section>
        <div className="space-y-5"><section className={panel}><h2 className="font-semibold">Наблюдения серверов</h2><p className="mt-2 text-sm text-gray-500 dark:text-gray-400">{data.hosts} входных хостов · {data.connections} серверных конфигураций</p><dl className="mt-4 space-y-2 text-sm">{[["healthy", "Статистика получена"], ["error", "Ошибка опроса"], ["stale", "Устаревшие данные"], ["unknown", "Нет наблюдений"]].map(([key, label]) => <div key={key} className="flex justify-between gap-3"><dt>{label}</dt><dd className="font-semibold tabular-nums">{data.observations[key as keyof typeof data.observations]}</dd></div>)}</dl><p className="mt-4 text-xs text-gray-500 dark:text-gray-400">Опрос статистики не подтверждает работу туннеля из сети пользователя.</p><Link href="/admin/servers" className="mt-3 inline-block text-sm text-brand-600 dark:text-brand-400 underline">Открыть серверы →</Link></section>
          <section className={panel}><h2 className="font-semibold">Исполнение доступа</h2><p className="mt-3 text-sm">Ожидают выдачи после оплаты балансом: <strong>{data.pending_access}</strong></p><p className="mt-2 text-sm">Незавершённые ротации: <strong>{data.pending_rotations}</strong></p><Link href="/admin/payments?attention=1" className="mt-3 inline-block text-sm text-brand-600 dark:text-brand-400 underline">Разбор платежей →</Link></section></div>
      </div>
    </>}
  </div>;
}
