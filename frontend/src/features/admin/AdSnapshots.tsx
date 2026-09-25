"use client";

import { useEffect, useState } from "react";
import { adminFetch } from "./api";

interface Placement {
  title: string;
  views: number;
  clicks: number;
  actions: number;
  cpm_ton: string;
  spent_ton_lines: string[];
  budget_ton_lines: string[];
  target: string;
  status: string;
  displayed_date: string;
  note: string;
}
interface Snapshot {
  source_ref: string;
  observed_date: string | null;
  created_at: string;
  usd_per_ton: string;
  notes: string;
  placements: Placement[];
  totals: { views: number; clicks: number; actions: number; spent_ton: string; spent_usd: string };
}
const number = new Intl.NumberFormat("ru-RU");
const statuses: Record<string, string> = { Active: "Активно", Stopped: "Остановлено", "On Hold": "На паузе" };

export function AdSnapshots() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    void adminFetch<{ ok: true; snapshot: Snapshot | null }>("/api/v1/admin/telegram-ads/snapshots").then((result) => {
      if (!active) return;
      if (result.ok) setSnapshot(result.data.snapshot);
      else setError(true);
      setLoading(false);
    });
    return () => { active = false; };
  }, []);
  return <section className="mt-6 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
    <div className="space-y-2 p-5">
      <h2 className="text-lg font-semibold">Срез рекламного кабинета</h2>
      <p className="text-sm text-gray-500">Накопительные показатели. Фильтр периода выше к этому блоку не применяется.</p>
      {loading && <p role="status">Загрузка среза…</p>}
      {error && <p role="alert">Не удалось загрузить срез. Обновите страницу.</p>}
      {!loading && !error && !snapshot && <p>Срезы ещё не внесены.</p>}
      {snapshot && <>
        <p className="text-sm">Дата среза: {snapshot.observed_date ?? "не подтверждена"}. Внесён: {new Date(snapshot.created_at).toLocaleDateString("ru-RU", { timeZone: "Europe/Moscow" })}.</p>
        <div className="grid gap-3 sm:grid-cols-3">
          <div><div className="text-sm text-gray-500">Расход по видимым строкам</div><strong>{snapshot.totals.spent_ton} TON · ${snapshot.totals.spent_usd}</strong></div>
          <div><div className="text-sm text-gray-500">Показы / клики</div><strong>{number.format(snapshot.totals.views)} / {number.format(snapshot.totals.clicks)}</strong></div>
          <div><div className="text-sm text-gray-500">Действия Telegram Ads</div><strong>{number.format(snapshot.totals.actions)}</strong><div className="text-xs">Не являются оплатами</div></div>
        </div>
        <p className="text-sm">Стоимость покупки: ${snapshot.usd_per_ton} за TON. Рублёвая окупаемость не рассчитана: курс RUB не указан.</p>
        <p className="text-xs text-gray-500">{snapshot.notes}</p>
      </>}
    </div>
    {snapshot && <div className="overflow-x-auto"><table className="w-full text-left text-sm">
      <thead className="bg-gray-50 dark:bg-zinc-900"><tr>{["Объявление", "Показы", "Клики / CTR", "Действия", "CPM, TON", "Расход, TON", "Остаток, TON", "Статус / таргет", "Дата в кабинете"].map((label) => <th key={label} className="whitespace-nowrap px-4 py-3">{label}</th>)}</tr></thead>
      <tbody>{snapshot.placements.map((row) => <tr key={row.title} className="border-t border-gray-100 dark:border-zinc-700">
        <td className="min-w-52 px-4 py-3"><strong>{row.title}</strong><p className="text-xs text-gray-500">{row.note}</p></td>
        <td className="px-4 py-3 tabular-nums">{number.format(row.views)}</td>
        <td className="whitespace-nowrap px-4 py-3 tabular-nums">{row.clicks} / {row.views ? `${(100 * row.clicks / row.views).toFixed(2)}%` : "—"}</td>
        <td className="px-4 py-3 tabular-nums">{row.actions}</td>
        <td className="px-4 py-3 tabular-nums">{row.cpm_ton}</td>
        <td className="px-4 py-3 tabular-nums">{row.spent_ton_lines.map((value, index) => <div key={index}>{value}</div>)}</td>
        <td className="px-4 py-3 tabular-nums">{row.budget_ton_lines.map((value, index) => <div key={index}>{value}</div>)}</td>
        <td className="whitespace-nowrap px-4 py-3">{statuses[row.status] ?? row.status}<p className="text-xs">{row.target}</p></td>
        <td className="whitespace-nowrap px-4 py-3">{row.displayed_date}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
