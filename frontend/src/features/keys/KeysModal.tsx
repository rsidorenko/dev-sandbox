"use client";

import { useEffect, useState } from "react";
import { userApi } from "@/entities/user/api";
import type { KeyEntry } from "@/entities/user/types";
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
  const { copy, isCopied } = useCopyToClipboard();

  useEffect(() => {
    if (!open || loaded) return;
    setLoading(true);
    userApi.keys().then((result) => {
      if (result.ok) {
        setKeys(result.data.keys);
        setSubUrl(result.data.subscription_url);
      }
      setLoading(false);
      setLoaded(true);
    });
  }, [open, loaded]);

  const handleReissue = async () => {
    setReissuing(true);
    const result = await userApi.reissueKeys();
    if (result.ok) {
      setKeys(result.data.keys);
      setSubUrl(result.data.subscription_url);
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
                  disabled={reissuing}
                  className="w-full rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 dark:border-red-800/50 dark:text-red-400 dark:hover:bg-red-900/20"
                >
                  Перевыпустить ключи
                </button>
              ) : (
                <div className="space-y-2">
                  <p className="text-sm text-red-600 dark:text-red-400">
                    Старые ключи перестанут работать. Продолжить?
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
