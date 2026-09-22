"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { adminNavActive, adminNavIdle } from "./ui";

const groups = [
  { label: "Работа с клиентами", links: [
    ["/admin/users", "Пользователи"],
    ["/admin/payments", "Платежи"],
    ["/admin/promocodes", "Промокоды"],
    ["/admin/plans", "Тарифы"],
    ["/admin/broadcast", "Рассылка"],
  ] },
  { label: "Сервис", links: [["/admin/servers", "Серверы"], ["/admin/incidents", "Инциденты и действия"]] },
  { label: "Аналитика", links: [["/admin/stats", "Статистика"]] },
];

function NavigationLinks({ close }: { close?: () => void }) {
  const pathname = usePathname();
  return <nav aria-label="Разделы админ-панели" className="space-y-6">
    {groups.map(group => <div key={group.label}>
      <p className="mb-2 px-3 text-xs font-semibold uppercase tracking-wide text-gray-400">{group.label}</p>
      <div className="flex flex-col gap-1">{group.links.map(([href, label]) => {
        const active = pathname === href || pathname.startsWith(`${href}/`);
        return <Link key={href} href={href} onClick={close} aria-current={active ? "page" : undefined}
          className={`${active ? adminNavActive : adminNavIdle} block py-2.5`}>{label}</Link>;
      })}</div>
    </div>)}
  </nav>;
}

export function AdminNavigation() {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const pathname = usePathname();
  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) { node.close(); trigger.current?.focus(); }
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [open]);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const closeOnDesktop = () => { if (media.matches) setOpen(false); };
    media.addEventListener("change", closeOnDesktop);
    return () => media.removeEventListener("change", closeOnDesktop);
  }, []);
  return <>
    <button ref={trigger} type="button" aria-label="Открыть меню админки" aria-haspopup="dialog"
      aria-expanded={open} onClick={() => setOpen(true)}
      className="fixed left-4 top-3 z-50 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900 lg:hidden">☰ Меню</button>
    <aside className="fixed bottom-0 left-0 top-[65px] hidden w-56 overflow-y-auto border-r border-gray-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-900 lg:block">
      <NavigationLinks />
    </aside>
    <dialog ref={dialog} aria-labelledby="admin-menu-title" onCancel={() => setOpen(false)}
      onKeyDown={event => {
        if (event.key !== "Tab") return;
        const controls = event.currentTarget.querySelectorAll<HTMLElement>('button, a[href]');
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}
      onClick={event => { if (event.target === event.currentTarget) setOpen(false); }}
      className="fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-72 max-w-[85vw] border-r border-gray-200 bg-white p-0 text-gray-900 backdrop:bg-black/40 dark:border-zinc-700 dark:bg-zinc-900 dark:text-white">
      <div className="min-h-full p-4">
        <div className="mb-8 flex items-center justify-between">
          <h2 id="admin-menu-title" className="font-semibold">Bravada · Админка</h2>
          <button type="button" aria-label="Закрыть меню" onClick={() => setOpen(false)} className="rounded-lg px-3 py-2 hover:bg-gray-100 dark:hover:bg-zinc-800">✕</button>
        </div>
        <NavigationLinks close={() => setOpen(false)} />
      </div>
    </dialog>
  </>;
}
