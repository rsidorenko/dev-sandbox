"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { adminApi } from "@/features/admin/api";
import { adminDate } from "@/features/admin/display";
import { FACET_NAMES, FACET_TITLES, facetValueLabel } from "@/features/admin/userFilters";
import { adminBtnSecondary, adminInput, adminRow, adminTable, adminTableWrap, adminTd, adminTh } from "@/features/admin/ui";
import type { AdminUsersResponse } from "@/entities/admin/types";

const PAGE_SIZE = 50;
const columns = { plan: "Тариф", expiry: "Срок (МСК)", balance: "Баланс", devices: "Устройства", type: "Тип" };
type Column = keyof typeof columns;
const allowed = ["search", "status", "plan", "balance", "trial", "devices", "kind", "sort", "page"];

export default function AdminUsersPage() {
  return <Suspense fallback={<p>Загрузка…</p>}><Users /></Suspense>;
}

function Users() {
  const router = useRouter();
  const params = useSearchParams();
  const query = params.toString();
  const [data, setData] = useState<AdminUsersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [visibleColumns, setVisibleColumns] = useState<Column[]>(Object.keys(columns) as Column[]);
  const [message, setMessage] = useState("");
  const requestId = useRef(0);
  const page = Math.max(1, Math.min(100000, Math.floor(Number(params.get("page"))) || 1));
  const change = (key: string, value: string) => {
    const next = new URLSearchParams(query);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== "page") next.delete("page");
    router.push(`/admin/users?${next}`);
  };
  const load = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true); setError("");
    const next = new URLSearchParams(query);
    const filters = Object.fromEntries(allowed.filter(key => key !== "page").map(key => [key, next.get(key) ?? ""]));
    const r = await adminApi.listUsers({ ...filters, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE });
    if (id !== requestId.current) return;
    if (!r.ok && r.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
      return;
    }
    if (r.ok) setData(r.data); else { setData(null); setError("Не удалось загрузить пользователей. Повторите запрос."); }
    setLoading(false);
  }, [query, page]);
  useEffect(() => { const counter = requestId; void load(); return () => { counter.current++; }; }, [load]);
  useEffect(() => { setSearch(params.get("search") ?? ""); }, [params]);
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("admin-user-columns") ?? "null");
      if (Array.isArray(saved)) setVisibleColumns(saved.filter((key): key is Column => key in columns));
    } catch { /* Повреждённые настройки не мешают работе. */ }
  }, []);
  const toggleColumn = (key: Column) => {
    const next = visibleColumns.includes(key) ? visibleColumns.filter(v => v !== key) : [...visibleColumns, key];
    setVisibleColumns(next);
    try { localStorage.setItem("admin-user-columns", JSON.stringify(next)); } catch { /* Настройка действует до обновления. */ }
  };
  const restore = () => {
    try {
      const saved = new URLSearchParams(localStorage.getItem("admin-user-filter") ?? "");
      const safe = new URLSearchParams();
      allowed.forEach(key => { const value = saved.get(key); if (value) safe.set(key, value); });
      router.push(`/admin/users?${safe}`);
      setMessage("Сохранённый фильтр применён.");
    } catch { setMessage("Не удалось прочитать сохранённый фильтр."); }
  };
  return <div className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h1 className="text-2xl font-bold">Пользователи</h1><p className="mt-1 text-sm text-gray-500">{loading ? "Загрузка…" : `Найдено: ${data?.total ?? 0}`} · поиск по всей базе</p></div>
      <button className={adminBtnSecondary} onClick={() => void load()} disabled={loading}>Обновить</button>
    </div>
    <form className="flex flex-wrap gap-2" onSubmit={event => { event.preventDefault(); change("search", search.trim()); }}>
      <input aria-label="Поиск пользователей" className={`${adminInput} min-w-0 flex-1`} value={search} onChange={event => setSearch(event.target.value)} placeholder="Telegram ID или внутренний ID" />
      <button className={adminBtnSecondary}>Найти</button>
    </form>
    <div className="flex flex-wrap items-start gap-2">
      {FACET_NAMES.map(name => <details key={name} className="rounded-lg border border-gray-200 bg-white p-2 dark:border-zinc-700 dark:bg-zinc-800">
        <summary className="cursor-pointer text-sm">{FACET_TITLES[name]}{params.get(name) ? " •" : ""}</summary>
        <div className="mt-2 space-y-1">{Array.from(new Set([...(data?.facets?.[name] ?? []).map(v => v.key), ...(params.get(name)?.split(",") ?? [])])).map(key => {
          const selected = params.get(name)?.split(",") ?? [];
          const count = data?.facets?.[name]?.find(v => v.key === key)?.count ?? 0;
          return <label key={key} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selected.includes(key)} onChange={() => change(name, (selected.includes(key) ? selected.filter(v => v !== key) : [...selected, key]).join(","))} />{facetValueLabel(name, key)} <span className="text-gray-400">{count}</span></label>;
        })}</div>
      </details>)}
      <select aria-label="Тип аккаунта" className={adminInput} value={params.get("kind") ?? ""} onChange={e => change("kind", e.target.value)}><option value="">Все аккаунты</option><option value="regular">Обычные</option><option value="internal">Тестовые</option></select>
      <select aria-label="Сортировка" className={adminInput} value={params.get("sort") ?? "id"} onChange={e => change("sort", e.target.value)}><option value="id">По Telegram ID ↓</option><option value="expiry">По сроку ↑</option><option value="balance">По балансу ↓</option></select>
      <details className="rounded-lg border p-2"><summary className="cursor-pointer text-sm">Колонки</summary>{Object.entries(columns).map(([key, label]) => <label key={key} className="flex gap-2 text-sm"><input type="checkbox" checked={visibleColumns.includes(key as Column)} onChange={() => toggleColumn(key as Column)} />{label}</label>)}</details>
    </div>
    <div className="flex flex-wrap gap-2 text-sm">
      <button className={adminBtnSecondary} onClick={() => { try { localStorage.setItem("admin-user-filter", query); setMessage("Фильтр сохранён в этом браузере."); } catch { setMessage("Браузер не разрешил сохранить фильтр."); } }}>Сохранить фильтр</button>
      <button className={adminBtnSecondary} onClick={restore}>Мой фильтр</button>
      <Link className={adminBtnSecondary} href="/admin/users">Сбросить</Link>
      {message && <span role="status" className="self-center text-gray-500">{message}</span>}
    </div>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    <div className={`${adminTableWrap} max-h-[65vh]`} aria-busy={loading}>
      <table className={adminTable}>
        <thead className="sticky top-0 z-10"><tr><th className={adminTh}>Пользователь</th><th className={adminTh}>Доступ</th>{visibleColumns.map(key => <th key={key} className={adminTh}>{columns[key]}</th>)}</tr></thead>
        <tbody>{!loading && data?.users.map(user => <tr key={user.internal_user_id} className={adminRow}>
          <td className={adminTd}><Link className="font-mono font-semibold text-brand-600 underline" href={`/admin/users/${encodeURIComponent(user.internal_user_id)}`}>{user.telegram_user_id}</Link><p className="text-xs text-gray-400">{user.is_web ? "Веб-аккаунт" : "Telegram"}</p></td>
          <td className={adminTd}>{user.is_active ? "Активен" : user.subscription_state === "cancelled" ? "Отменён" : user.subscription_state ? "Неактивен" : "Нет подписки"}</td>
          {visibleColumns.map(key => <td key={key} className={`${adminTd} tabular-nums`}>{({ plan: user.plan_id ?? "—", expiry: user.active_until ? `${adminDate(user.active_until)} (${user.remaining_days ?? 0} дн.)` : "—", balance: `${user.balance_rubles.toFixed(2)} ₽`, devices: user.device_count ?? "—", type: user.is_internal ? "Тестовый" : "Обычный" })[key]}</td>)}
        </tr>)}</tbody>
      </table>
      {loading && <p className="p-8 text-center">Загрузка…</p>}
      {!loading && data?.users.length === 0 && <p className="p-8 text-center text-gray-500">По выбранным условиям пользователей нет.</p>}
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
      <p className="text-gray-500">Источник: БД · получено {adminDate(data?.observed_at)} МСК</p>
      <div className="flex items-center gap-3"><button className={adminBtnSecondary} disabled={loading || page <= 1} onClick={() => change("page", String(page - 1))}>Назад</button><span>Страница {page} / {Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE))}</span><button className={adminBtnSecondary} disabled={loading || page * PAGE_SIZE >= (data?.total ?? 0)} onClick={() => change("page", String(page + 1))}>Далее</button></div>
    </div>
  </div>;
}
