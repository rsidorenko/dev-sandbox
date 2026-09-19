"use client";

import { useParams } from "next/navigation";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ConnectionCheckCard } from "@/features/connection-check/ConnectionCheckCard";

export default function ConnectionCheckPage() {
  const params = useParams<{ token: string }>();
  const token = typeof params?.token === "string" ? params.token : "";

  return (
    <>
      <PageHeader
        title="Проверка подключения"
        description="Автоматически проверим, идёт ли трафик через защищённый сервер"
      />
      <section className="mx-auto max-w-xl px-4 py-12">
        {token ? (
          <ConnectionCheckCard token={token} />
        ) : (
          <div className="rounded-2xl border border-gray-100 bg-white p-8 text-center shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
            <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">
              Ссылка недействительна
            </h1>
            <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
              Проверьте ссылку или запросите новую в Telegram-боте.
            </p>
          </div>
        )}
      </section>
    </>
  );
}
