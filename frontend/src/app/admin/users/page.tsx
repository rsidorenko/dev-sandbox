"use client";

import { useCallback, useEffect, useState } from "react";
import { adminApi } from "@/features/admin/api";
import { AddDaysModal } from "@/features/admin/AddDaysModal";
import { StatusLegend } from "@/features/admin/StatusLegend";
import {
  adminBtnPrimary,
  adminBtnSecondary,
  adminInput,
  adminMono,
  adminNum,
  adminRow,
  adminTable,
  adminTd,
  adminTh,
  adminTableWrap,
} from "@/features/admin/ui";
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
  const [addDaysUser, setAddDaysUser] = useState<AdminUser | null>(null);

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
  const hasFilters = Boolean(search);

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Пользователи</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Всего записей: {total}</p>
        </div>
        <form onSubmit={onSearch} className="flex gap-2">
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="email или telegram id"
            className={`${adminInput} w-64`}
          />
          <button type="submit" className={adminBtnPrimary}>
            Найти
          </button>
          {hasFilters && (
            <button
              type="button"
              onClick={() => {
                setSearchInput("");
                setSearch("");
                setOffset(0);
              }}
              className={adminBtnSecondary}
            >
              Сброс
            </button>
          )}
        </form>
      </div>

      {error && <Alert className="mb-4">{error}</Alert>}

      <StatusLegend />

      <div className={adminTableWrap}>
        <table className={adminTable}>
          <thead>
            <tr>
              <th className={`${adminTh} w-36`}>Telegram ID</th>
              <th className={adminTh}>Email</th>
              <th className={`${adminTh} w-28`}>Статус</th>
              <th className={`${adminTh} w-20`}>Тариф</th>
              <th className={`${adminTh} w-28`}>До</th>
              <th className={`${adminTh} w-28 text-right`}>Баланс</th>
              <th className={`${adminTh} w-16 text-center`}>Триал</th>
              <th className={`${adminTh} w-28 text-right`}>Действия</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-gray-400">
                  Загрузка…
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-gray-400">
                  {hasFilters ? "Ничего не найдено" : "Нет пользователей"}
                </td>
              </tr>
            ) : (
              users.map((u) => (
                <tr key={u.internal_user_id} className={adminRow}>
                  <td className={`${adminTd} ${adminMono} text-xs text-gray-600 dark:text-gray-400`}>
                    {u.telegram_user_id}
                  </td>
                  <td className={`${adminTd} max-w-[16rem] truncate`}>{u.email ?? "—"}</td>
                  <td className={adminTd}>
                    <StateBadge state={u.subscription_state} isActive={u.is_active} />
                  </td>
                  <td className={`${adminTd} ${adminNum}`}>{u.plan_id ?? "—"}</td>
                  <td className={`${adminTd} ${adminNum}`}>
                    {u.remaining_days != null ? `${u.remaining_days} дн.` : u.active_until ?? "—"}
                  </td>
                  <td className={`${adminTd} ${adminNum} text-right`}>{u.balance_rubles.toFixed(2)} ₽</td>
                  <td className={`${adminTd} text-center text-gray-500`}>{u.trial_used ? "исп." : "—"}</td>
                  <td className={`${adminTd} text-right`}>
                    <button
                      type="button"
                      disabled={!u.subscription_state}
                      title={
                        u.subscription_state
                          ? "Добавить дни без смены тарифа"
                          : "Нет подписки для продления (никогда не активировал)"
                      }
                      onClick={() => setAddDaysUser(u)}
                      className="text-sm font-semibold text-brand-600 transition hover:text-brand-700 disabled:cursor-not-allowed disabled:text-gray-300 dark:disabled:text-zinc-600"
                    >
                      + Дни
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {addDaysUser && (
        <AddDaysModal user={addDaysUser} onClose={() => setAddDaysUser(null)} onSaved={() => void load()} />
      )}

      <div className="mt-4 flex items-center justify-between text-sm text-gray-500 dark:text-gray-400">
        <span>
          Стр. {Math.floor(offset / LIMIT) + 1} из {pages}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - LIMIT))}
            className={adminBtnSecondary}
          >
            ← Назад
          </button>
          <button
            type="button"
            disabled={offset + LIMIT >= total}
            onClick={() => setOffset(offset + LIMIT)}
            className={adminBtnSecondary}
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
    ? "bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-400"
    : "bg-gray-100 text-gray-600 dark:bg-zinc-700 dark:text-gray-300";
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${color}`}>{label}</span>
  );
}
