"use client";

import { useCallback, useEffect, useState } from "react";
import { adminApi } from "@/features/admin/api";
import { AddDaysModal } from "@/features/admin/AddDaysModal";
import { StatusLegend } from "@/features/admin/StatusLegend";
import { GROUPING_OPTIONS, groupUsers, type UserGroup, type UserGrouping } from "@/features/admin/groupUsers";
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

const PAGE_LIMIT = 50;
const GROUPED_LIMIT = 1000; // грузим всех в режиме группировки (cap _MAX_LIMIT на бэке)

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [groupBy, setGroupBy] = useState<UserGrouping>("none");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [addDaysUser, setAddDaysUser] = useState<AdminUser | null>(null);

  const grouped = groupBy !== "none";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const r = await adminApi.listUsers({
      limit: grouped ? GROUPED_LIMIT : PAGE_LIMIT,
      offset: grouped ? 0 : offset,
      search: search || undefined,
    });
    if (r.ok) {
      setUsers(r.data.users);
      setTotal(r.data.total);
    } else if (r.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent("/admin/users")}`;
    } else {
      setError("Не удалось загрузить пользователей.");
    }
    setLoading(false);
  }, [offset, search, grouped]);

  useEffect(() => {
    void load();
  }, [load]);

  const onSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setOffset(0);
    setSearch(searchInput.trim());
  };

  const onChangeGrouping = (next: UserGrouping) => {
    setGroupBy(next);
    setOffset(0);
    setCollapsed(new Set()); // ключи бакетов перекрываются между измерениями — сбрасываем
  };

  const toggleGroup = (key: string) => {
    setCollapsed((prev) => {
      const nextSet = new Set(prev);
      if (nextSet.has(key)) nextSet.delete(key);
      else nextSet.add(key);
      return nextSet;
    });
  };

  const pages = Math.max(1, Math.ceil(total / PAGE_LIMIT));
  const hasFilters = Boolean(search);
  const groups: UserGroup[] = grouped ? groupUsers(users, groupBy) : [];
  const shownUsersCount = grouped ? groups.reduce((s, g) => s + g.users.length, 0) : users.length;

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Пользователи</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Всего записей: {total}
            {grouped && shownUsersCount < total && (
              <span className="text-amber-600 dark:text-amber-400"> (показаны первые {GROUPED_LIMIT})</span>
            )}
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <label className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
            Группировка
            <select
              value={groupBy}
              onChange={(e) => onChangeGrouping(e.target.value as UserGrouping)}
              className={`${adminInput} w-44`}
            >
              {GROUPING_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <form onSubmit={onSearch} className="flex gap-2">
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="email или telegram id"
              className={`${adminInput} w-56`}
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
            ) : grouped ? (
              groups.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-gray-400">
                    {hasFilters ? "Ничего не найдено" : "Нет пользователей"}
                  </td>
                </tr>
              ) : (
                groups.flatMap((g) => {
                  const isCollapsed = collapsed.has(g.key);
                  const rows = [
                    <GroupHeaderRow
                      key={`grp-${g.key}`}
                      group={g}
                      collapsed={isCollapsed}
                      onToggle={() => toggleGroup(g.key)}
                    />,
                  ];
                  if (!isCollapsed) {
                    for (const u of g.users) {
                      rows.push(
                        <UserRow key={u.internal_user_id} u={u} onAddDays={() => setAddDaysUser(u)} />,
                      );
                    }
                  }
                  return rows;
                })
              )
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-10 text-center text-gray-400">
                  {hasFilters ? "Ничего не найдено" : "Нет пользователей"}
                </td>
              </tr>
            ) : (
              users.map((u) => <UserRow key={u.internal_user_id} u={u} onAddDays={() => setAddDaysUser(u)} />)
            )}
          </tbody>
        </table>
      </div>

      {addDaysUser && (
        <AddDaysModal user={addDaysUser} onClose={() => setAddDaysUser(null)} onSaved={() => void load()} />
      )}

      {!grouped && (
        <div className="mt-4 flex items-center justify-between text-sm text-gray-500 dark:text-gray-400">
          <span>
            Стр. {Math.floor(offset / PAGE_LIMIT) + 1} из {pages}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_LIMIT))}
              className={adminBtnSecondary}
            >
              ← Назад
            </button>
            <button
              type="button"
              disabled={offset + PAGE_LIMIT >= total}
              onClick={() => setOffset(offset + PAGE_LIMIT)}
              className={adminBtnSecondary}
            >
              Вперёд →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function GroupHeaderRow({
  group,
  collapsed,
  onToggle,
}: {
  group: UserGroup;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <tr className="border-b border-gray-200 bg-gray-50/80 dark:border-zinc-700 dark:bg-zinc-800/60">
      <td colSpan={8} className="px-4 py-2.5">
        <button
          type="button"
          onClick={onToggle}
          className="flex w-full items-center gap-2 text-left text-sm font-semibold text-gray-700 dark:text-gray-200"
        >
          <span className="text-gray-400 dark:text-gray-500">{collapsed ? "▶" : "▼"}</span>
          <span>{group.label}</span>
          <span className="rounded-md bg-gray-200/70 px-2 py-0.5 text-xs font-medium text-gray-600 dark:bg-zinc-700 dark:text-gray-300">
            {group.users.length}
          </span>
          <span className={`${adminNum} text-xs font-normal text-gray-500 dark:text-gray-400`}>
            Σ {group.sumBalance.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽
          </span>
        </button>
      </td>
    </tr>
  );
}

function UserRow({ u, onAddDays }: { u: AdminUser; onAddDays: () => void }) {
  return (
    <tr className={adminRow}>
      <td className={`${adminTd} ${adminMono} text-xs text-gray-600 dark:text-gray-400`}>{u.telegram_user_id}</td>
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
          onClick={onAddDays}
          className="text-sm font-semibold text-brand-600 transition hover:text-brand-700 disabled:cursor-not-allowed disabled:text-gray-300 dark:disabled:text-zinc-600"
        >
          + Дни
        </button>
      </td>
    </tr>
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
