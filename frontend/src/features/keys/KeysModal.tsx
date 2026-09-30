"use client";

import { useEffect, useState } from "react";
import { userApi } from "@/entities/user/api";
import type { KeyEntry, RotationStatus } from "@/entities/user/types";
import { useCopyToClipboard } from "@/shared/hooks/useCopyToClipboard";
import { Modal } from "@/shared/ui/Modal";

type Props = {
  open: boolean;
  onClose: () => void;
};

export function KeysModal({ open, onClose }: Props) {
  const [keys, setKeys] = useState<KeyEntry[]>([]);
  const [subUrl, setSubUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [reissueConfirm, setReissueConfirm] = useState(false);
  const [reissuing, setReissuing] = useState(false);
  const [rotation, setRotation] = useState<RotationStatus>("idle");
  const [error, setError] = useState("");
  const { copy, isCopied } = useCopyToClipboard();

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    userApi.keys().then((result) => {
      if (cancelled) return;
      if (result.ok) {
        setKeys(result.data.keys);
        setSubUrl(result.data.subscription_url);
        setRotation(result.data.rotation_status ?? "idle");
        setError("");
      } else {
        setError("Не удалось загрузить ключи. Закройте окно и попробуйте снова.");
      }
      setLoading(false);
      setLoaded(true);
    });
    return () => { cancelled = true; };
  }, [open]);

  const pending = ["provisioning", "waiting", "retiring"].includes(rotation);
  useEffect(() => {
    if (!open || !pending) return;
    let cancelled = false;
    const timer = setInterval(() => {
      userApi.keys().then((result) => {
        if (cancelled || !result.ok) return;
        setKeys(result.data.keys);
        setSubUrl(result.data.subscription_url);
        setRotation(result.data.rotation_status ?? "idle");
      });
    }, 15000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [open, pending]);

  const handleReissue = async () => {
    setReissuing(true);
    setError("");
    const result = await userApi.reissueKeys();
    if (result.ok) {
      setKeys(result.data.keys);
      setSubUrl(result.data.subscription_url);
      setRotation(result.data.rotation_status ?? "waiting");
    } else {
      setError("Перевыпуск пока не подтверждён. Обновите статус или повторите попытку: новое поколение повторно не создастся.");
      const current = await userApi.keys();
      if (current.ok) setRotation(current.data.rotation_status ?? "idle");
    }
    setReissuing(false);
    setReissueConfirm(false);
  };

  if (!open) return null;

  return (
    <Modal onClose={onClose}>
      <div className="w-full max-w-lg max-h-[85vh] overflow-y-auto rounded-2xl bg-white p-6 shadow-xl dark:bg-zinc-800">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900 dark:text-gray-100">
            Настройки подключения
          </h2>
          <button
            onClick={() => {
              onClose();
              setReissueConfirm(false);
            }}
            className="text-gray-400 transition hover:text-gray-600 dark:hover:text-gray-200"
          >
            ✕
          </button>
        </div>

        {error && <p role="alert" className="mt-4 text-sm text-red-600">{error}</p>}
        {rotation !== "idle" && (
          <p role="status" className="mt-4 text-sm text-gray-600 dark:text-gray-300">
            {rotation === "provisioning" && "Создаём новые ключи. Старые пока продолжают работать."}
            {rotation === "waiting" && "Новые ключи готовы. Обновите подписку в VPN-приложении. После обнаружения первого трафика по новым ключам старые автоматически удалятся на всех серверах. Проверка выполняется периодически. Обновите подписку и на остальных устройствах. Ссылка подписки остаётся прежней."}
            {rotation === "retiring" && "Трафик по новым ключам обнаружен. Завершаем удаление старых ключей на серверах."}
            {rotation === "completed" && "Переход завершён: старые ключи удалены. На всех устройствах используйте обновлённую подписку."}
          </p>
        )}

        {!loaded && !loading && <p className="mt-6 text-center text-gray-500 dark:text-gray-400">Загрузка ключей...</p>}

        {loading ? (
          <p className="mt-6 text-center text-gray-500 dark:text-gray-400">
            Загрузка ключей...
          </p>
        ) : keys.length === 0 ? (
          <p className="mt-6 text-center text-gray-500 dark:text-gray-400">
            Ключи не найдены
          </p>
        ) : (
          <>
            {subUrl && (
              <div className="mt-4 rounded-lg bg-brand-50 p-4 dark:bg-brand-950/30">
                <p className="text-xs font-medium text-brand-700 dark:text-brand-300">
                  Ссылка для совместимых приложений
                </p>
                <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
                  Все ключи подтянутся автоматически
                </p>
                <button
                  onClick={() => copy(subUrl, "sub-url")}
                  className="mt-2 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700"
                >
                  {isCopied("sub-url") ? "Скопировано!" : "Скопировать ссылку"}
                </button>
              </div>
            )}

            <div className="mt-4 space-y-3">
              {keys.map((k, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-gray-100 p-3 dark:border-zinc-600"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {k.flag} {k.label}
                    </span>
                    <button
                      onClick={() => copy(k.link, `key-${i}`)}
                      className="rounded-lg bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-200 dark:bg-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-600"
                    >
                      {isCopied(`key-${i}`) ? "Скопировано!" : "Копировать"}
                    </button>
                  </div>
                  <p className="mt-1 truncate text-xs text-gray-400 font-mono">
                    {k.link}
                  </p>
                </div>
              ))}
            </div>

            <div className="mt-4 border-t border-gray-100 pt-4 dark:border-zinc-700">
              {!reissueConfirm ? (
                <button
                  onClick={() => setReissueConfirm(true)}
                  disabled={reissuing || pending}
                  className="w-full rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 dark:border-red-800/50 dark:text-red-400 dark:hover:bg-red-900/20"
                >
                  {pending ? "Перевыпуск выполняется" : "Перевыпустить ключи"}
                </button>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm text-red-600 dark:text-red-400">
                    Будут созданы новые ключи. Старые удалятся на всех серверах после обнаружения первого трафика по новым. На всех устройствах потребуется обновить подписку; сама ссылка останется прежней. Продолжить?
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={handleReissue}
                      disabled={reissuing}
                      className="flex-1 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
                    >
                      {reissuing ? "Перевыпуск..." : "Да, перевыпустить"}
                    </button>
                    <button
                      onClick={() => setReissueConfirm(false)}
                      className="flex-1 rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50 dark:border-zinc-600 dark:text-zinc-300 dark:hover:bg-zinc-700"
                    >
                      Отмена
                    </button>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
