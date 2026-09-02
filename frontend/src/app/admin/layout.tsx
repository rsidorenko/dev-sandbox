"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AdminAuthProvider, useAdminAuth } from "@/features/admin/AdminAuthProvider";
import { adminNavActive, adminNavIdle } from "@/features/admin/ui";

function SunIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className={className}>
      <path d="M10 2a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 2zM10 15a.75.75 0 01.75.75v1.5a.75.75 0 01-1.5 0v-1.5A.75.75 0 0110 15zM10 7a3 3 0 100 6 3 3 0 000-6zM15.657 5.404a.75.75 0 10-1.06-1.06l-1.061 1.06a.75.75 0 001.06 1.06l1.06-1.06zM6.464 14.596a.75.75 0 10-1.06-1.06l-1.06 1.06a.75.75 0 001.06 1.06l1.06-1.06zM18 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 0118 10zM5 10a.75.75 0 01-.75.75h-1.5a.75.75 0 010-1.5h1.5A.75.75 0 015 10zM14.596 15.657a.75.75 0 001.06-1.06l-1.06-1.061a.75.75 0 10-1.06 1.06l1.06 1.06zM5.404 6.464a.75.75 0 001.06-1.06l-1.06-1.06a.75.75 0 10-1.06 1.06l1.06 1.06z" />
    </svg>
  );
}

function MoonIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className={className}>
      <path fillRule="evenodd" d="M7.455 2.004a.75.75 0 01.26.77 7 7 0 009.958 7.967.75.75 0 011.067.853A8.5 8.5 0 116.647 1.921a.75.75 0 01.808.083z" clipRule="evenodd" />
    </svg>
  );
}

// Переключатель темы — та же логика localStorage, что в шапке сайта (Header.tsx).
function ThemeToggle() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Сменить тему"
      className="rounded-lg p-2 text-gray-500 transition hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-zinc-800"
    >
      {dark ? <SunIcon className="h-5 w-5" /> : <MoonIcon className="h-5 w-5" />}
    </button>
  );
}

function LoadingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 dark:bg-zinc-900">
      <div className="flex items-center gap-3 text-gray-500 dark:text-gray-400">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" />
        <span className="text-sm">Загрузка…</span>
      </div>
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
      <Link href={href} className={active ? adminNavActive : adminNavIdle}>
        {label}
      </Link>
    );
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-zinc-900">
      <header className="sticky top-0 z-40 border-b border-gray-200 bg-white/80 backdrop-blur-md dark:border-zinc-700 dark:bg-zinc-900/80">
        <div className="mx-auto grid max-w-7xl grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 px-4 py-3 sm:px-6 lg:flex lg:gap-4">
          <div className="flex min-w-0 items-baseline gap-2 lg:order-1 lg:shrink-0">
            <span className="text-lg font-bold text-brand-700 dark:text-brand-400">Bravada</span>
            <span className="hidden text-xs font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500 sm:inline">
              Админ-панель
            </span>
          </div>
          <nav
            aria-label="Разделы админ-панели"
            className="col-span-2 flex min-w-0 gap-1 overflow-x-auto pb-1 pt-1 [-webkit-overflow-scrolling:touch] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden lg:order-2 lg:col-span-1 lg:flex-1 lg:justify-center lg:overflow-visible lg:py-0"
          >
            {navLink("/admin/users", "Пользователи")}
            {navLink("/admin/promocodes", "Промокоды")}
            {navLink("/admin/plans", "Тарифы")}
            {navLink("/admin/broadcast", "Рассылка")}
            {navLink("/admin/stats", "Статистика")}
          </nav>
          <div className="flex shrink-0 items-center gap-1.5 justify-self-end lg:order-3">
            <ThemeToggle />
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded-lg border border-gray-200 px-3 py-1.5 text-sm font-medium text-gray-600 transition hover:bg-gray-100 dark:border-zinc-600 dark:text-gray-300 dark:hover:bg-zinc-700"
            >
              Выйти
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">{children}</main>
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
