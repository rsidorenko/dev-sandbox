"use client";

import { useEffect, useState } from "react";
import { adminFetch } from "./api";

interface Row {
  job_id: string; created_at: string; first_recorded_at: string | null;
  claimed_users: number; selected_users: number; payment_users: number;
  payments_created: number; paid_users: number; payments_succeeded: number; revenue_kopecks: number;
}
export function BroadcastFunnel() {
  const [rows, setRows] = useState<Row[]>([]);
  const [state, setState] = useState("loading");
  useEffect(() => {
    let live = true;
    void adminFetch<{ ok: true; jobs: Row[] }>("/api/v1/admin/broadcast-funnel").then((result) => {
      if (!live) return;
      if (result.ok) { setRows(result.data.jobs); setState("ready"); }
      else setState("error");
    });
    return () => { live = false; };
  }, []);
  return <section className="mt-6 rounded-2xl border border-gray-200 bg-white p-5 dark:border-zinc-700 dark:bg-zinc-800">
    <h2 className="text-lg font-semibold">Бонус рассылки → оплата</h2>
    <p className="mt-2 text-sm text-gray-500">Последние 50 рассылок с промокодом. Уникальные пользователи быстрого Telegram-checkout; другие пути оплаты сюда не приписываются. События собираются с внедрения блока, история не восстановлена.</p>
    {state === "loading" && <p role="status">Загрузка…</p>}
    {state === "error" && <p role="alert">Не удалось загрузить воронку. Обновите страницу.</p>}
    {state === "ready" && rows.length === 0 && <p className="mt-3">Рассылок с промокодом пока нет.</p>}
    {rows.length > 0 && <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm">
      <thead><tr>{["Рассылка", "Получили бонус", "Выбрали тариф", "Создали счёт", "Оплатили", "Счета / оплаты", "Выручка"].map((label) => <th className="whitespace-nowrap p-3" key={label}>{label}</th>)}</tr></thead>
      <tbody>{rows.map((r) => <tr key={r.job_id} className="border-t border-gray-100 dark:border-zinc-700">
        <td className="p-3"><time>{new Date(r.created_at).toLocaleDateString("ru-RU", { timeZone: "Europe/Moscow" })}</time><div className="font-mono text-xs" title={r.job_id}>{r.job_id.slice(0, 8)}</div>{!r.first_recorded_at && <p className="text-xs text-gray-500">Нет записанных событий</p>}</td>
        {[r.claimed_users, r.selected_users, r.payment_users, r.paid_users].map((n, i) => <td className="p-3 tabular-nums" key={i}>{r.first_recorded_at ? n : "—"}</td>)}
        <td className="p-3 tabular-nums">{r.first_recorded_at ? `${r.payments_created} / ${r.payments_succeeded}` : "—"}</td>
        <td className="whitespace-nowrap p-3 tabular-nums">{r.first_recorded_at ? `${(r.revenue_kopecks / 100).toFixed(2)} ₽` : "—"}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
