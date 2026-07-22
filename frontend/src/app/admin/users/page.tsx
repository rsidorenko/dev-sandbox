"use client";

import { useCallback, useEffect, useState } from "react";
import { adminApi } from "@/features/admin/api";
import type { AdminUser } from "@/entities/admin/types";
import { Alert } from "@/shared/ui/Alert";

const LIMIT = 50;

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const r = await adminApi.listUsers({ limit: LIMIT, offset, search: search || undefined });
    if (r.ok) {
      setUsers(r.data.users);
      setTotal(r.data.total);
    } else if (r.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent("/admin/users")}`;
    } else {
      setError("Не удалось загрузить пользователей.");
    }
    setLoading(false);
  }, [offset, search]);

  useEffect(() => {
    void load();
  }, [load]);

  const onSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setOffset(0);
    setSearch(searchInput.trim());
  };

  const pages = Math.max(1, Math.ceil(total / LIMIT));

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Пользователи</h1>
        <form onSubmit={onSearch} className="flex gap-2">
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="email или telegram id"
            className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100"
          />
          <button
            type="submit"
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700"
          >
            Найти
          </button>
          {search && (
            <button
              type="button"
              onClick={() => {
                setSearchInput("");
                setSearch("");
                setOffset(0);
              }}
              className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 dark:border-zinc-600 dark:text-gray-300"
            >
              Сброс
            </button>
          )}
        </form>
      </div>

      {error && <Alert className="mb-4">{error}</Alert>}

      <div className="overflow-x-auto rounded-2xl border border-gray-100 bg-white dark:border-zinc-700 dark:bg-zinc-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-gray-100 text-xs uppercase text-gray-500 dark:border-zinc-700">
            <tr>
              <th className="px-3 py-2">Telegram ID</th>
              <th className="px-3 py-2">Email</th>
              <th className="px-3 py-2">Статус</th>
              <th className="px-3 py-2">Тариф</th>
              <th className="px-3 py-2">До</th>
              <th className="px-3 py-2">Баланс</th>
              <th className="px-3 py-2">Триал</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-gray-400">
                  Загрузка…
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-gray-400">
                  Нет пользователей
                </td>
              </tr>
            ) : (
              users.map((u) => (
                <tr key={u.internal_user_id} className="border-b border-gray-50 last:border-0 dark:border-zinc-700/50">
                  <td className="px-3 py-2 font-mono text-xs text-gray-700 dark:text-gray-300">{u.telegram_user_id}</td>
                  <td className="px-3 py-2 text-gray-700 dark:text-gray-300">{u.email ?? "—"}</td>
                  <td className="px-3 py-2">
                    <StateBadge state={u.subscription_state} isActive={u.is_active} />
                  </td>
                  <td className="px-3 py-2 text-gray-700 dark:text-gray-300">{u.plan_id ?? "—"}</td>
                  <td className="px-3 py-2 text-gray-700 dark:text-gray-300">
                    {u.remaining_days != null ? `${u.remaining_days} дн.` : u.active_until ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-gray-700 dark:text-gray-300">{u.balance_rubles.toFixed(2)} ₽</td>
                  <td className="px-3 py-2 text-gray-500">{u.trial_used ? "исп." : "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
        <span>
          Всего: {total} · стр. {Math.floor(offset / LIMIT) + 1}/{pages}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - LIMIT))}
            className="rounded-lg border border-gray-200 px-3 py-1.5 disabled:opacity-40 dark:border-zinc-600"
          >
            ← Назад
          </button>
          <button
            type="button"
            disabled={offset + LIMIT >= total}
            onClick={() => setOffset(offset + LIMIT)}
            className="rounded-lg border border-gray-200 px-3 py-1.5 disabled:opacity-40 dark:border-zinc-600"
          >
            Вперёд →
          </button>
        </div>
      </div>
    </div>
  );
}

function StateBadge({ state, isActive }: { state: string | null; isActive: boolean }) {
  const label = isActive ? "active" : state ?? "—";
  const color = isActive
    ? "bg-green-100 text-green-700 dark:bg-green-950/30 dark:text-green-400"
    : "bg-gray-100 text-gray-600 dark:bg-zinc-700 dark:text-gray-300";
  return <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${color}`}>{label}</span>;
}
