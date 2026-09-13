"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { adminApi } from "@/features/admin/api";
import { AddDaysModal } from "@/features/admin/AddDaysModal";
import { StatusLegend } from "@/features/admin/StatusLegend";
import { InternalAccountModal, ResetTestAccountModal } from "@/features/admin/TestAccountModals";
import {
  FACET_NAMES,
  FACET_TITLES,
  computeFacets,
  emptyFilters,
  facetValueLabel,
  filterUsers,
  hasFilters,
  type FacetName,
  type Filters,
} from "@/features/admin/userFilters";
import {
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

const LOAD_LIMIT = 1000; // грузим всех один раз; фильтр/поиск/фасеты — мгновенно на клиенте

export default function AdminUsersPage() {
  const [allUsers, setAllUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [search, setSearch] = useState("");
  const [collapsedFacets, setCollapsedFacets] = useState<Set<FacetName>>(new Set());
  const [addDaysUser, setAddDaysUser] = useState<AdminUser | null>(null);
  const [internalUser, setInternalUser] = useState<AdminUser | null>(null);
  const [resetUser, setResetUser] = useState<AdminUser | null>(null);
  const [actionMessage, setActionMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const r = await adminApi.listUsers({ limit: LOAD_LIMIT });
    if (r.ok) {
      setAllUsers(r.data.users);
      setTotal(r.data.total);
    } else if (r.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent("/admin/users")}`;
    } else {
      setError("Не удалось загрузить пользователей.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Поиск + фильтры — на клиенте по полной загрузке.
  const searched = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allUsers;
    return allUsers.filter(
      (u) => (u.email ?? "").toLowerCase().includes(q) || String(u.telegram_user_id).includes(q),
    );
  }, [allUsers, search]);

  const facets = useMemo(() => computeFacets(searched, filters), [searched, filters]);
  const visible = useMemo(() => filterUsers(searched, filters), [searched, filters]);

  const toggleFilter = (facet: FacetName, key: string) => {
    setFilters((prev) => {
      const nextSet = new Set(prev[facet]);
      if (nextSet.has(key)) nextSet.delete(key);
      else nextSet.add(key);
      return { ...prev, [facet]: nextSet };
    });
  };

  const toggleFacetCollapse = (facet: FacetName) => {
    setCollapsedFacets((prev) => {
      const nextSet = new Set(prev);
      if (nextSet.has(facet)) nextSet.delete(facet);
      else nextSet.add(facet);
      return nextSet;
    });
  };

  const resetAll = () => {
    setFilters(emptyFilters());
    setSearch("");
  };

  const anyFilterActive = hasFilters(filters) || Boolean(search);

  return (
    <div className="flex flex-col gap-6 lg:flex-row">
      {/* Панель фильтров */}
      <aside className="lg:w-64 lg:shrink-0">
        <div className="rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3 dark:border-zinc-700">
            <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">Фильтры</span>
            {anyFilterActive && (
              <button type="button" onClick={resetAll} className="text-xs font-medium text-brand-600 hover:text-brand-700">
                Сбросить всё
              </button>
            )}
          </div>
          <div className="max-h-[70vh] overflow-y-auto">
            {FACET_NAMES.map((facet) => {
              const opts = facets[facet];
              const collapsed = collapsedFacets.has(facet);
              return (
                <div key={facet} className="border-b border-gray-100 last:border-0 dark:border-zinc-700/60">
                  <button
                    type="button"
                    onClick={() => toggleFacetCollapse(facet)}
                    className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wider text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-zinc-700/40"
                  >
                    <span className="text-gray-400 dark:text-gray-500">{collapsed ? "▶" : "▼"}</span>
                    <span>{FACET_TITLES[facet]}</span>
                    {filters[facet].size > 0 && (
                      <span className="rounded bg-brand-100 px-1.5 text-[10px] font-bold text-brand-700 dark:bg-brand-900/40 dark:text-brand-300">
                        {filters[facet].size}
                      </span>
                    )}
                  </button>
                  {!collapsed && (
                    <div className="px-4 pb-2">
                      {opts.length === 0 ? (
                        <div className="py-1 text-xs text-gray-400">Нет значений</div>
                      ) : (
                        opts.map((o) => {
                          const checked = filters[facet].has(o.key);
                          return (
                            <label
                              key={o.key}
                              className="flex cursor-pointer items-center gap-2 py-1 text-sm text-gray-700 dark:text-gray-300"
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleFilter(facet, o.key)}
                                className="h-4 w-4 rounded border-gray-300 text-brand-600 focus:ring-brand-500 dark:border-zinc-500 dark:bg-zinc-700"
                              />
                              <span className={checked ? "font-medium text-gray-900 dark:text-gray-100" : ""}>{o.label}</span>
                              <span className={`${adminNum} ml-auto text-xs text-gray-400 dark:text-gray-500`}>{o.count}</span>
                            </label>
                          );
                        })
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </aside>

      {/* Список */}
      <div className="min-w-0 flex-1">
        <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Пользователи</h1>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              {loading
                ? "Загрузка…"
                : `Показано ${visible.length} из ${total}${total > LOAD_LIMIT ? ` (первые ${LOAD_LIMIT})` : ""}`}
            </p>
          </div>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="поиск: email или telegram id"
            className={`${adminInput} w-full sm:w-72`}
          />
        </div>

        {error && <Alert className="mb-4">{error}</Alert>}
        {actionMessage && <Alert variant="info" className="mb-4">{actionMessage}</Alert>}

        <StatusLegend />

        {anyFilterActive && (
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <span className="text-xs text-gray-400">Активные:</span>
            {FACET_NAMES.flatMap((facet) =>
              Array.from(filters[facet]).map((key) => (
                <button
                  key={`${facet}-${key}`}
                  type="button"
                  onClick={() => toggleFilter(facet, key)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-xs text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-gray-300 dark:hover:bg-zinc-700"
                >
                  <span className="text-gray-400 dark:text-gray-500">{FACET_TITLES[facet]}:</span>
                  {facetValueLabel(facet, key)}
                  <span className="text-gray-400 dark:text-gray-500">×</span>
                </button>
              )),
            )}
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-xs text-gray-700 transition hover:bg-gray-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-gray-300 dark:hover:bg-zinc-700"
              >
                <span className="text-gray-400 dark:text-gray-500">поиск:</span>
                {search}
                <span className="text-gray-400 dark:text-gray-500">×</span>
              </button>
            )}
          </div>
        )}

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
                <th className={`${adminTh} w-24 text-center`}>Тип</th>
                <th className={`${adminTh} w-52 text-right`}>Действия</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={9} className="px-4 py-10 text-center text-gray-400">
                    Загрузка…
                  </td>
                </tr>
              ) : visible.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-4 py-10 text-center text-gray-400">
                    {anyFilterActive ? "Под фльтр ничего не подходит" : "Нет пользователей"}
                  </td>
                </tr>
              ) : (
                visible.map((u) => (
                  <UserRow
                    key={u.internal_user_id}
                    u={u}
                    onAddDays={() => setAddDaysUser(u)}
                    onToggleInternal={() => setInternalUser(u)}
                    onReset={() => setResetUser(u)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>

        {addDaysUser && (
          <AddDaysModal user={addDaysUser} onClose={() => setAddDaysUser(null)} onSaved={() => void load()} />
        )}
        {internalUser && (
          <InternalAccountModal
            user={internalUser}
            onClose={() => setInternalUser(null)}
            onSaved={() => {
              setActionMessage(internalUser.is_internal ? "Тестовая метка снята." : "Аккаунт помечен тестовым.");
              setInternalUser(null);
              void load();
            }}
          />
        )}
        {resetUser && (
          <ResetTestAccountModal
            user={resetUser}
            onClose={() => setResetUser(null)}
            onReset={(result) => {
              setActionMessage(
                `Тестовый аккаунт ${result.telegram_user_id} сброшен. Теперь отправьте боту /start.`,
              );
              setResetUser(null);
              void load();
            }}
          />
        )}
      </div>
    </div>
  );
}

function UserRow({
  u,
  onAddDays,
  onToggleInternal,
  onReset,
}: {
  u: AdminUser;
  onAddDays: () => void;
  onToggleInternal: () => void;
  onReset: () => void;
}) {
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
      <td className={`${adminTd} text-center`}>
        <span
          className={u.is_internal
            ? "inline-flex rounded-md bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-400"
            : "text-xs text-gray-400"}
        >
          {u.is_internal ? "Тестовый" : "Обычный"}
        </span>
      </td>
      <td className={`${adminTd} text-right`}>
        <div className="flex items-center justify-end gap-3 whitespace-nowrap">
          <button
            type="button"
            title="Грант дней подписки (продлить или создать)"
            onClick={onAddDays}
            className="text-sm font-semibold text-brand-600 transition hover:text-brand-700"
          >
            + Дни
          </button>
          <button
            type="button"
            onClick={onToggleInternal}
            className="text-xs font-medium text-gray-500 transition hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
          >
            {u.is_internal ? "Снять метку" : "Сделать тестовым"}
          </button>
          {u.is_internal && (
            <button
              type="button"
              onClick={onReset}
              className="text-xs font-semibold text-red-600 transition hover:text-red-700 dark:text-red-400"
            >
              Сбросить
            </button>
          )}
        </div>
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
