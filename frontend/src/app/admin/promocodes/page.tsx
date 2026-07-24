"use client";

import { useCallback, useEffect, useState } from "react";
import { adminApi } from "@/features/admin/api";
import { PromocodeFormModal } from "@/features/admin/PromocodeFormModal";
import {
  adminBtnPrimary,
  adminBtnSecondary,
  adminMono,
  adminNum,
  adminRow,
  adminTable,
  adminTd,
  adminTh,
  adminTableWrap,
} from "@/features/admin/ui";
import type { AdminPromocode } from "@/entities/admin/types";
import { Alert } from "@/shared/ui/Alert";

export default function AdminPromocodesPage() {
  const [items, setItems] = useState<AdminPromocode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<AdminPromocode | null>(null);
  const [busyCode, setBusyCode] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const r = await adminApi.listPromocodes();
    if (r.ok) {
      setItems(r.data.promocodes);
    } else if (r.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent("/admin/promocodes")}`;
    } else {
      setError("Не удалось загрузить промокоды.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };
  const openEdit = (p: AdminPromocode) => {
    setEditing(p);
    setModalOpen(true);
  };

  const onDelete = async (p: AdminPromocode) => {
    if (!window.confirm(`Удалить промокод ${p.code}?`)) return;
    setBusyCode(p.code);
    const r = await adminApi.deletePromocode(p.code);
    if (!r.ok) {
      setError(r.error === "promo_code_not_found" ? "Промокод не найден." : "Не удалось удалить.");
    } else {
      void load();
    }
    setBusyCode(null);
  };

  return (
    <div>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900 dark:text-gray-100">Промокоды</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Всего записей: {items.length}</p>
        </div>
        <button type="button" onClick={openCreate} className={adminBtnPrimary}>
          + Создать промокод
        </button>
      </div>

      {error && <Alert className="mb-4">{error}</Alert>}

      <div className={adminTableWrap}>
        <table className={adminTable}>
          <thead>
            <tr>
              <th className={`${adminTh} w-40`}>Код</th>
              <th className={`${adminTh} w-32`}>Для кого</th>
              <th className={`${adminTh} w-24 text-right`}>Скидка</th>
              <th className={`${adminTh} w-32 text-right`}>Погашений</th>
              <th className={`${adminTh} w-28`}>Срок</th>
              <th className={`${adminTh} w-24`}>Статус</th>
              <th className={`${adminTh} w-36 text-right`}>Действия</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-gray-400">
                  Загрузка…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-gray-400">
                  Промокодов пока нет
                </td>
              </tr>
            ) : (
              items.map((p) => (
                <tr key={p.code} className={adminRow}>
                  <td className={`${adminTd} ${adminMono} font-semibold text-gray-900 dark:text-gray-100`}>{p.code}</td>
                  <td className={adminTd}>{p.audience === "new_users" ? "новые" : "не новые"}</td>
                  <td className={`${adminTd} ${adminNum} text-right`}>
                    {p.discount_kind === "percent" ? `${p.discount_value}%` : `${p.discount_value} ₽`}
                  </td>
                  <td className={`${adminTd} ${adminNum} text-right`}>
                    {p.redemptions_count}
                    <span className="text-gray-400 dark:text-gray-500">
                      {p.max_redemptions != null ? ` / ${p.max_redemptions}` : " / ∞"}
                    </span>
                  </td>
                  <td className={`${adminTd} ${adminNum} text-gray-500 dark:text-gray-400`}>
                    {p.expires_at ? new Date(p.expires_at).toLocaleDateString("ru-RU") : "∞"}
                  </td>
                  <td className={adminTd}>
                    <span
                      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${
                        p.is_active
                          ? "bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-400"
                          : "bg-gray-100 text-gray-500 dark:bg-zinc-700 dark:text-gray-400"
                      }`}
                    >
                      {p.is_active ? "активен" : "выкл"}
                    </span>
                  </td>
                  <td className={`${adminTd} text-right`}>
                    <div className="flex justify-end gap-3">
                      <button
                        type="button"
                        onClick={() => openEdit(p)}
                        className="text-sm font-semibold text-brand-600 transition hover:text-brand-700"
                      >
                        Изменить
                      </button>
                      <button
                        type="button"
                        disabled={busyCode === p.code}
                        onClick={() => void onDelete(p)}
                        className="text-sm font-semibold text-red-600 transition hover:text-red-700 disabled:opacity-40"
                      >
                        Удалить
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {modalOpen && (
        <PromocodeFormModal initial={editing} onClose={() => setModalOpen(false)} onSaved={() => void load()} />
      )}
    </div>
  );
}
