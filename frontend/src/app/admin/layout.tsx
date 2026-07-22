"use client";

import { useEffect, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AdminAuthProvider, useAdminAuth } from "@/features/admin/AdminAuthProvider";

function LoadingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-zinc-900">
      <p className="text-gray-500">Загрузка…</p>
    </div>
  );
}

function AdminGate({ children }: { children: ReactNode }) {
  const { loading, authenticated, logout } = useAdminAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname === "/admin/login";

  useEffect(() => {
    if (!isLogin && !loading && !authenticated) {
      const next = encodeURIComponent(pathname);
      router.replace(`/admin/login?next=${next}`);
    }
  }, [isLogin, loading, authenticated, pathname, router]);

  if (isLogin) return <>{children}</>;
  if (loading) return <LoadingScreen />;
  if (!authenticated) return null;

  const navLink = (href: string, label: string) => {
    const active = pathname === href || pathname.startsWith(`${href}/`);
    return (
      <Link
        href={href}
        className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
          active
            ? "bg-brand-600 text-white"
            : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-zinc-800"
        }`}
      >
        {label}
      </Link>
    );
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-zinc-900">
      <header className="border-b border-gray-200 bg-white dark:border-zinc-700 dark:bg-zinc-800">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <span className="text-sm font-bold uppercase tracking-wide text-gray-900 dark:text-gray-100">
              Bravada · Админка
            </span>
            <nav className="flex gap-1">
              {navLink("/admin/users", "Пользователи")}
              {navLink("/admin/promocodes", "Промокоды")}
            </nav>
          </div>
          <button
            type="button"
            onClick={() => void logout()}
            className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-600 transition hover:bg-gray-100 dark:border-zinc-600 dark:text-gray-300 dark:hover:bg-zinc-700"
          >
            Выйти
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <AdminAuthProvider>
      <AdminGate>{children}</AdminGate>
    </AdminAuthProvider>
  );
}
