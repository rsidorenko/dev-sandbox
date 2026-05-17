"use client";

import Link from "next/link";
import { useState, useEffect } from "react";

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

export function Header() {
  const [open, setOpen] = useState(false);
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  };

  const links = [
    { href: "/#how-it-works", label: "Как это работает" },
    { href: "/#tariffs", label: "Тарифы" },
    { href: "/#faq", label: "FAQ" },
    { href: "/contacts", label: "Контакты" },
  ];

  const themeIcon = dark
    ? <SunIcon className="h-5 w-5" />
    : <MoonIcon className="h-5 w-5" />;

  return (
    <header className="sticky top-0 z-50 border-b border-gray-100 bg-white/80 backdrop-blur-md dark:border-zinc-700 dark:bg-zinc-900/80">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
        <Link href="/" className="text-xl font-bold text-brand-700 dark:text-brand-400">
          Bravada
        </Link>

        {/* Desktop nav */}
        <nav className="hidden items-center gap-6 md:flex">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="text-sm font-medium text-gray-600 transition hover:text-brand-600 dark:text-gray-300 dark:hover:text-brand-400"
            >
              {l.label}
            </Link>
          ))}
          <button
            onClick={toggleTheme}
            className="rounded-lg p-2 text-gray-500 transition hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
            aria-label="Сменить тему"
          >
            {themeIcon}
          </button>
          <Link
            href="/dashboard"
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-700"
          >
            Личный кабинет
          </Link>
        </nav>

        {/* Mobile */}
        <div className="flex items-center gap-2 md:hidden">
          <button
            onClick={toggleTheme}
            className="rounded-lg p-2 text-gray-500 transition hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
            aria-label="Сменить тему"
          >
            {themeIcon}
          </button>
          <button
            className="flex flex-col gap-1.5"
            onClick={() => setOpen(!open)}
            aria-label="Меню"
          >
            <span className={`block h-0.5 w-6 bg-gray-700 transition dark:bg-zinc-300 ${open ? "translate-y-2 rotate-45" : ""}`} />
            <span className={`block h-0.5 w-6 bg-gray-700 transition dark:bg-zinc-300 ${open ? "opacity-0" : ""}`} />
            <span className={`block h-0.5 w-6 bg-gray-700 transition dark:bg-zinc-300 ${open ? "-translate-y-2 -rotate-45" : ""}`} />
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      {open && (
        <nav className="border-t border-gray-100 bg-white px-4 pb-4 dark:border-zinc-700 dark:bg-zinc-900">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              onClick={() => setOpen(false)}
              className="block py-3 text-sm font-medium text-gray-600 transition hover:text-brand-600 dark:text-gray-300 dark:hover:text-brand-400"
            >
              {l.label}
            </Link>
          ))}
          <Link
            href="/dashboard"
            onClick={() => setOpen(false)}
            className="mt-2 block rounded-lg bg-brand-600 px-4 py-2 text-center text-sm font-semibold text-white transition hover:bg-brand-700"
          >
            Личный кабинет
          </Link>
        </nav>
      )}
    </header>
  );
}
