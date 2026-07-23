"use client";

import { useState } from "react";
import { Modal } from "@/shared/ui/Modal";
import { Alert } from "@/shared/ui/Alert";
import { adminApi } from "./api";
import type { AdminUser } from "@/entities/admin/types";

const MIN_DAYS = 1;
const MAX_DAYS = 3650;

function fmt(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("ru-RU");
  } catch {
    return iso;
  }
}

export function AddDaysModal({
  user,
  onClose,
  onSaved,
}: {
  user: AdminUser;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [days, setDays] = useState("7");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<string | null>(null);

  const submit = async () => {
    setError("");
    setResult(null);
    const n = Number(days);
    if (!Number.isInteger(n) || n < MIN_DAYS || n > MAX_DAYS) {
      setError(`Введите целое число дней от ${MIN_DAYS} до ${MAX_DAYS}.`);
      return;
    }
    setSubmitting(true);
    const r = await adminApi.addDays(user.internal_user_id, n);
    setSubmitting(false);
    if (!r.ok) {
      setError(mapError(r.error));
      return;
    }
    setResult(
      `Добавлено ${r.data.days_added} дн. Новый срок: ${fmt(r.data.new_active_until)}. ` +
        `Тариф без изменений: ${r.data.plan_unchanged ?? "—"}. Серверов синхронизировано: ${r.data.servers_ok}.`,
    );
    onSaved();
  };

  const inputClass =
    "mt-1 block w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100";

  return (
    <Modal onClose={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-gray-100 bg-white p-6 shadow-xl dark:border-zinc-700 dark:bg-zinc-800">
        <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">Добавить дни подписки</h2>
        <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">
          Telegram ID {user.telegram_user_id} · тариф {user.plan_id ?? "—"} · до {fmt(user.active_until)}
        </div>
        <p className="mt-2 text-xs text-gray-500">
          Тариф и число устройств не меняются — продлевается только срок подписки, ключи синхронизируются на серверах.
        </p>

        {error && <Alert className="mt-4">{error}</Alert>}
        {result && <Alert className="mt-4">{result}</Alert>}

        <div className="mt-4">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300" htmlFor="add-days-input">
            Дней
          </label>
          <input
            id="add-days-input"
            type="number"
            min={MIN_DAYS}
            max={MAX_DAYS}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            className={inputClass}
          />
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-zinc-600 dark:text-gray-300 dark:hover:bg-zinc-700"
          >
            Закрыть
          </button>
          <button
            type="button"
            disabled={submitting}
            onClick={() => void submit()}
            className="rounded-xl bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {submitting ? "Сохранение..." : "Добавить дни"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function mapError(error: string): string {
  switch (error) {
    case "no_subscription_to_extend":
      return "У пользователя нет подписки для продления (никогда не активировал).";
    case "user_not_found":
      return "Пользователь не найден.";
    case "invalid_days":
      return "Некорректное число дней.";
    case "unauthorized":
      return "Сессия истекла — перевойдите в админку.";
    case "csrf_validation_failed":
      return "CSRF-ошибка. Обновите страницу и повторите.";
    default:
      return "Не удалось добавить дни. Попробуйте позже.";
  }
}
