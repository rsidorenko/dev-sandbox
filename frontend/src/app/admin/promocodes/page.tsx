"use client";

import { useCallback, useEffect, useState } from "react";
import { adminApi } from "@/features/admin/api";
import { PromocodeFormModal } from "@/features/admin/PromocodeFormModal";
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
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Промокоды</h1>
        <button
          type="button"
          onClick={openCreate}
          className="rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-700"
        >
          + Создать промокод
        </button>
      </div>

      {error && <Alert className="mb-4">{error}</Alert>}

      <div className="overflow-x-auto rounded-2xl border border-gray-100 bg-white dark:border-zinc-700 dark:bg-zinc-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-gray-100 text-xs uppercase text-gray-500 dark:border-zinc-700">
            <tr>
              <th className="px-3 py-2">Код</th>
              <th className="px-3 py-2">Для кого</th>
              <th className="px-3 py-2">Скидка</th>
              <th className="px-3 py-2">Погашений</th>
              <th className="px-3 py-2">Срок</th>
              <th className="px-3 py-2">Статус</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-gray-400">
                  Загрузка…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-gray-400">
                  Промокодов пока нет
                </td>
              </tr>
            ) : (
              items.map((p) => (
                <tr key={p.code} className="border-b border-gray-50 last:border-0 dark:border-zinc-700/50">
                  <td className="px-3 py-2 font-mono font-semibold text-gray-900 dark:text-gray-100">{p.code}</td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-300">
                    {p.audience === "new_users" ? "новые" : "не новые"}
                  </td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-300">
                    {p.discount_kind === "percent" ? `${p.discount_value}%` : `${p.discount_value} ₽`}
                  </td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-300">
                    {p.redemptions_count}
                    {p.max_redemptions != null ? ` / ${p.max_redemptions}` : " / ∞"}
                  </td>
                  <td className="px-3 py-2 text-gray-500">
                    {p.expires_at ? new Date(p.expires_at).toLocaleDateString("ru-RU") : "∞"}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${
                        p.is_active
                          ? "bg-green-100 text-green-700 dark:bg-green-950/30 dark:text-green-400"
                          : "bg-gray-100 text-gray-500 dark:bg-zinc-700"
                      }`}
                    >
                      {p.is_active ? "активен" : "выкл"}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => openEdit(p)}
                      className="mr-2 text-sm font-medium text-brand-600 hover:text-brand-700"
                    >
                      Изменить
                    </button>
                    <button
                      type="button"
                      disabled={busyCode === p.code}
                      onClick={() => void onDelete(p)}
                      className="text-sm font-medium text-red-600 hover:text-red-700 disabled:opacity-40"
                    >
                      Удалить
                    </button>
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
