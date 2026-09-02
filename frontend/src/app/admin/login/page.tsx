"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { adminApi } from "@/features/admin/api";
import { Alert } from "@/shared/ui/Alert";
import { safeNextPath } from "@/shared/lib/safeRedirect";

const schema = z.object({
  username: z.string().min(1, "Введите логин"),
  password: z.string().min(1, "Введите пароль"),
});
type FormValues = z.infer<typeof schema>;

function AdminLoginForm() {
  const searchParams = useSearchParams();
  const [serverError, setServerError] = useState("");

  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { username: "", password: "" } });

  const onSubmit = form.handleSubmit(async (v) => {
    setServerError("");
    const r = await adminApi.login(v.username.trim(), v.password);
    if (!r.ok) {
      if (r.error === "invalid_credentials") {
        setServerError("Неверный логин или пароль.");
      } else if (r.error === "admin_disabled" || r.error === "admin_not_configured") {
        setServerError("Админ-панель отключена. Обратитесь к администратору.");
      } else {
        setServerError("Не удалось войти. Попробуйте позже.");
      }
      return;
    }
    const next = safeNextPath(searchParams.get("next"));
    // Полная перезагрузка, чтобы middleware увидел куку admin_session.
    window.location.href = next;
  });

  const inputClass =
    "mt-1 block w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100";

  return (
    <section className="flex min-h-screen items-center justify-center bg-gray-50 px-4 dark:bg-zinc-900">
      <div className="w-full max-w-md rounded-2xl border border-gray-100 bg-white p-5 shadow-sm dark:border-zinc-700 dark:bg-zinc-800 sm:p-8">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Вход в админку</h1>
        <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">Логин и пароль администратора</p>

        {serverError && <Alert className="mt-4">{serverError}</Alert>}

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <div>
            <label htmlFor="username" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Логин
            </label>
            <input
              id="username"
              autoComplete="username"
              autoFocus
              className={inputClass}
              disabled={form.formState.isSubmitting}
              {...form.register("username")}
            />
            {form.formState.errors.username && (
              <p className="mt-1 text-xs text-red-600">{form.formState.errors.username.message}</p>
            )}
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Пароль
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              className={inputClass}
              disabled={form.formState.isSubmitting}
              {...form.register("password")}
            />
            {form.formState.errors.password && (
              <p className="mt-1 text-xs text-red-600">{form.formState.errors.password.message}</p>
            )}
          </div>
          <button
            type="submit"
            disabled={form.formState.isSubmitting}
            className="w-full rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
          >
            {form.formState.isSubmitting ? "Вход…" : "Войти"}
          </button>
        </form>
      </div>
    </section>
  );
}

export default function AdminLoginPage() {
  return (
    <Suspense
      fallback={
        <section className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-zinc-900">
          <p className="text-gray-500">Загрузка…</p>
        </section>
      }
    >
      <AdminLoginForm />
    </Suspense>
  );
}
