"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { userApi } from "@/entities/user/api";
import { clearToken, parseLoginToken, setToken } from "@/shared/lib/tokenStore";
import { safeNextPath } from "@/shared/lib/safeRedirect";

type Status = "checking" | "invalid";

function AuthByToken() {
  const params = useParams<{ token: string }>();
  // Next.js отдаёт динамический сегмент URL-закодированным — декодируем.
  const rawToken = params.token ? decodeURIComponent(params.token) : "";
  const searchParams = useSearchParams();
  const router = useRouter();
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    const token = parseLoginToken(rawToken);
    if (!token) {
      setStatus("invalid");
      return;
    }
    // Токен в память, валидируем профилем (Bearer). router.replace заменяет
    // запись истории с токеном на целевой путь — ссылка не остаётся ни в
    // адресной строке, ни в back-истории (ручной replaceState перед навигацией
    // не нужен: в dev/StrictMode двойной эффект ронял навигацию и возвращал
    // URL с токеном).
    setToken(token);
    let cancelled = false;
    userApi.profile().then((res) => {
      if (cancelled) return;
      if (res.ok) {
        router.replace(safeNextPath(searchParams.get("next")));
      } else {
        clearToken();
        // Негодный токен вычищаем из адресной строки без навигации.
        window.history.replaceState(null, "", "/auth");
        setStatus("invalid");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [rawToken, router, searchParams]);

  if (status === "checking") {
    return (
      <section className="flex min-h-[70vh] items-center justify-center">
        <p className="text-gray-500 dark:text-gray-400">Проверяем ссылку...</p>
      </section>
    );
  }

  return (
    <section className="flex min-h-[70vh] items-center justify-center py-20">
      <div className="mx-auto w-full max-w-md px-4 text-center">
        <div className="rounded-2xl border border-gray-100 bg-white p-8 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
            Ссылка недействительна
          </h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Ссылка отозвана, введена с ошибкой или срок неоплаченной ссылки истёк.
            Если вы уже оплатили, обратитесь в поддержку с номером платежа.
          </p>
          <Link
            href="/login"
            className="mt-6 inline-block rounded-xl bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition hover:bg-brand-700"
          >
            На страницу входа
          </Link>
        </div>
      </div>
    </section>
  );
}

export default function AuthTokenPage() {
  return (
    <Suspense
      fallback={
        <section className="flex min-h-[70vh] items-center justify-center">
          <p className="text-gray-500">Загрузка...</p>
        </section>
      }
    >
      <AuthByToken />
    </Suspense>
  );
}
