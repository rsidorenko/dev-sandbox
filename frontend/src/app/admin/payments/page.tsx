"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAdminRead } from "@/features/admin/useAdminRead";
import { adminDate } from "@/features/admin/display";
import { adminBtnSecondary, adminInput, adminRow, adminTable, adminTableWrap, adminTd, adminTh } from "@/features/admin/ui";
import type { AdminPaymentsResponse } from "@/entities/admin/types";

const labels: Record<string, string> = {
  accepted: "Факт принят", duplicate: "Повтор", ignored: "Факт проигнорирован",
  active_applied: "Подписка применена", no_activation: "Без активации", needs_review: "Нужна проверка",
  not_recorded: "Нет записи применения", not_applicable: "Отдельный сценарий устройств",
  applied_atomically: "Применено при списании", legacy_unknown: "Нет сведений в журнале",
  unknown: "Нет подтверждения выдачи", pending: "Ожидает выдачи", done: "Выдача завершена", superseded: "Задание больше не актуально",
  missing_user: "Не определён пользователь", apply_needs_review: "Проверить применение подписки",
  subscription_not_applied: "Подписка не применена более 10 минут", access_delayed: "Выдача ожидает более 10 минут",
};
export default function AdminPaymentsPage() {
  return <Suspense fallback={<p>Загрузка…</p>}><Payments /></Suspense>;
}
function Payments() {
  const params = useSearchParams();
  const router = useRouter();
  const query = params.toString();
  const { data, loading, error, refresh } = useAdminRead<AdminPaymentsResponse>(`/api/v1/admin/payments?${query}`);
  const [search, setSearch] = useState(params.get("search") ?? "");
  useEffect(() => { setSearch(params.get("search") ?? ""); }, [params]);
  const change = (name: string, value: string) => {
    const next = new URLSearchParams(query);
    if (value) next.set(name, value); else next.delete(name);
    if (name !== "page") next.delete("page");
    router.push(`/admin/payments?${next}`);
  };
  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-bold">Платежи и выдача доступа</h1><button className={adminBtnSecondary} disabled={loading} onClick={() => void refresh()}>Обновить</button></div>
    <p className="text-sm text-gray-500">Зарегистрированные факты внешних оплат и списания с баланса. Незавершённые платежи, не дошедшие до системы, здесь не отображаются. Выдача — отдельный этап; активная подписка не подтверждает работу VPN.</p>
    <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); change("search", search.trim()); }}><input className={`${adminInput} min-w-0 flex-1`} aria-label="Поиск платежей" value={search} onChange={e => setSearch(e.target.value)} placeholder="ID пользователя или платежа" /><button className={adminBtnSecondary}>Найти</button></form>
    <div className="flex flex-wrap items-center gap-3">
      <select className={adminInput} aria-label="Период платежей" value={params.get("days") ?? "30"} onChange={e => change("days", e.target.value)}>{[1, 7, 30, 90].map(days => <option key={days} value={days}>Последние {days} дн.</option>)}</select>
      <select className={adminInput} aria-label="Способ оплаты" value={params.get("provider") ?? ""} onChange={e => change("provider", e.target.value)}><option value="">Все способы</option><option value="yookassa">ЮKassa</option><option value="cryptobot">CryptoBot</option><option value="balance">Баланс</option></select>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={params.get("attention") === "1"} onChange={e => change("attention", e.target.checked ? "1" : "")} />Требует внимания</label>
      <Link className={adminBtnSecondary} href="/admin/payments">Сбросить</Link>
    </div>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {loading && <p role="status">Загрузка…</p>}
    {!loading && data && <>
      <p className="text-sm text-gray-500">Найдено: {data.total} · источник: журналы оплаты и выдачи · получено {adminDate(data.observed_at)} МСК</p>
      <div className={`${adminTableWrap} max-h-[65vh]`}><table className={adminTable}><thead className="sticky top-0 z-10"><tr>{["Платёж / дата МСК", "Пользователь", "Сумма", "Оплата", "Подписка", "Доступ", "Внимание"].map(label => <th key={label} className={adminTh}>{label}</th>)}</tr></thead><tbody>{data.payments.map(p => <tr key={p.row_id} className={adminRow}>
        <td className={adminTd}><p className="max-w-64 break-all font-mono text-xs">{p.reference}</p><p className="text-xs text-gray-500">{p.provider} · {adminDate(p.occurred_at)}</p></td>
        <td className={adminTd}>{p.internal_user_id ? <Link className="text-brand-600 underline" href={`/admin/users/${encodeURIComponent(p.internal_user_id)}`}>{p.telegram_user_id && p.telegram_user_id > 0 ? p.telegram_user_id : p.telegram_user_id !== null ? "Веб-аккаунт" : p.internal_user_id}</Link> : "Не определён"}</td>
        <td className={`${adminTd} whitespace-nowrap tabular-nums`}>{p.amount_minor_units === null ? "Неизвестна" : p.currency_code === "RUB" ? `${(p.amount_minor_units / 100).toFixed(2)} ₽` : `${p.amount_minor_units} мин. ед. ${p.currency_code ?? "(валюта неизвестна)"}`}</td>
        <td className={adminTd}>{labels[p.payment_status] ?? p.payment_status}</td><td className={adminTd}>{labels[p.apply_status] ?? p.apply_status}</td>
        <td className={adminTd}><p>{labels[p.access_status] ?? p.access_status}</p>{p.access_attempts !== null && <p className="text-xs text-gray-500">Попыток: {p.access_attempts}{p.access_status === "pending" && ` · повтор ${adminDate(p.access_next_attempt_at)}`}</p>}</td>
        <td className={`${adminTd} text-amber-700 dark:text-amber-400`}>{p.attention_reason ? labels[p.attention_reason] ?? p.attention_reason : "—"}</td>
      </tr>)}</tbody></table>{data.payments.length === 0 && <p className="p-8 text-center text-gray-500">Платёжных фактов по выбранным условиям нет.</p>}</div>
      <div className="flex flex-wrap items-center justify-end gap-3 text-sm"><button className={adminBtnSecondary} disabled={data.page <= 1} onClick={() => change("page", String(data.page - 1))}>Назад</button><span>Страница {data.page} / {Math.max(1, Math.ceil(data.total / data.limit))}</span><button className={adminBtnSecondary} disabled={data.page * data.limit >= data.total} onClick={() => change("page", String(data.page + 1))}>Далее</button></div>
      <p className="text-xs text-gray-500">«Выдача завершена» — результат задания на момент исполнения, не текущая доступность VPN. Для внешних оплат отдельного подтверждения выдачи в журнале пока нет. Балансовые списания не прибавляются к внешней выручке.</p>
    </>}
  </div>;
}
