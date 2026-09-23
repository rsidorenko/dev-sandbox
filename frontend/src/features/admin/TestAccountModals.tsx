"use client";

import { useState } from "react";
import type { AdminTestUserResetResponse, AdminUser } from "@/entities/admin/types";
import { Alert } from "@/shared/ui/Alert";
import { Modal } from "@/shared/ui/Modal";
import { adminBtnPrimary, adminBtnSecondary } from "./ui";
import { adminApi } from "./api";

const inputClass =
  "mt-1 block w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100";

function mapError(error: string): string {
  switch (error) {
    case "user_not_found":
      return "Пользователь уже удалён или не найден.";
    case "test_account_required":
      return "Сброс разрешён только для аккаунта, помеченного тестовым.";
    case "confirmation_mismatch":
    case "invalid_confirmation":
      return "Telegram ID не совпадает с выбранным пользователем.";
    case "invalid_reason":
      return "Укажите причину длиной до 300 символов.";
    case "vpn_delete_failed":
      return "VPN-доступ не удалось удалить со всех серверов. Данные аккаунта сохранены.";
    case "csrf_validation_failed":
      return "CSRF-ошибка. Обновите страницу и повторите.";
    case "unauthorized":
      return "Сессия истекла — перевойдите в админку.";
    default:
      return "Операция не выполнена. Попробуйте позже.";
  }
}

export function InternalAccountModal({
  user,
  onClose,
  onSaved,
}: {
  user: AdminUser;
  onClose: () => void;
  onSaved: () => void;
}) {
  const nextValue = !user.is_internal;
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    const cleanReason = reason.trim();
    if (!cleanReason) return;
    setSubmitting(true);
    setError("");
    const response = await adminApi.setInternal(user.internal_user_id, nextValue, cleanReason);
    setSubmitting(false);
    if (!response.ok) {
      setError(mapError(response.error));
      return;
    }
    onSaved();
  };

  return (
    <Modal onClose={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-gray-100 bg-white p-5 shadow-xl dark:border-zinc-700 dark:bg-zinc-800">
        <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">
          {nextValue ? "Пометить тестовым" : "Снять тестовую метку"}
        </h2>
        <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
          Telegram ID {user.telegram_user_id}. Тестовые аккаунты исключаются из бизнес-аналитики и могут быть полностью сброшены.
        </p>
        {error && <Alert className="mt-4">{error}</Alert>}
        <label className="mt-4 block text-sm font-medium text-gray-700 dark:text-gray-300" htmlFor="internal-reason">
          Причина
          <textarea
            id="internal-reason"
            maxLength={300}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Например: аккаунт для проверки Telegram-бота"
            className={`${inputClass} min-h-24 resize-y`}
          />
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={adminBtnSecondary}>Отмена</button>
          <button
            type="button"
            disabled={submitting || !reason.trim()}
            onClick={() => void submit()}
            className={adminBtnPrimary}
          >
            {submitting ? "Сохраняем…" : nextValue ? "Пометить" : "Снять метку"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function ResetTestAccountModal({
  user,
  onClose,
  onReset,
}: {
  user: AdminUser;
  onClose: () => void;
  onReset: (result: AdminTestUserResetResponse) => void;
}) {
  const [confirmation, setConfirmation] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const confirmed = confirmation.trim() === String(user.telegram_user_id);
  const canSubmit = confirmed && Boolean(reason.trim()) && !submitting;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError("");
    const response = await adminApi.resetTestAccount(
      user.internal_user_id,
      Number(confirmation.trim()),
      reason.trim(),
    );
    setSubmitting(false);
    if (!response.ok) {
      setError(mapError(response.error));
      return;
    }
    onReset(response.data);
  };

  return (
    <Modal onClose={onClose}>
      <div className="w-full max-w-lg rounded-2xl border border-red-100 bg-white p-5 shadow-xl dark:border-red-950 dark:bg-zinc-800">
        <h2 className="text-xl font-bold text-red-700 dark:text-red-400">Сбросить тестовый аккаунт</h2>
        <div className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/30 dark:text-red-300">
          Будут удалены триал, подписка, ключи на всех VPN-серверах, собственная платежная и
          реферальная история, промокоды, трафик и события воронки. Начисления другим
          пользователям сохраняются. Не запускайте одновременно оплату или бот.
        </div>
        <p className="mt-3 text-sm text-gray-600 dark:text-gray-300">
          После сброса отправьте боту <code>/start</code>: аккаунт будет создан заново и снова увидит предложение триала.
        </p>
        {error && <Alert className="mt-4">{error}</Alert>}
        <label className="mt-4 block text-sm font-medium text-gray-700 dark:text-gray-300" htmlFor="reset-telegram-id">
          Введите Telegram ID {user.telegram_user_id}
          <input
            id="reset-telegram-id"
            inputMode="numeric"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            autoComplete="off"
            className={inputClass}
          />
        </label>
        <label className="mt-4 block text-sm font-medium text-gray-700 dark:text-gray-300" htmlFor="reset-reason">
          Причина
          <textarea
            id="reset-reason"
            maxLength={300}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Например: повторная проверка trial flow"
            className={`${inputClass} min-h-24 resize-y`}
          />
        </label>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={onClose} className={adminBtnSecondary}>Отмена</button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => void submit()}
            className="inline-flex items-center justify-center rounded-lg bg-red-600 px-3.5 py-2 text-sm font-semibold text-white transition hover:bg-red-700 disabled:opacity-40"
          >
            {submitting ? "Сбрасываем…" : "Удалить и сбросить"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
