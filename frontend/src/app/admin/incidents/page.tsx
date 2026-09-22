"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { adminFetch } from "@/features/admin/api";
import { useAdminRead } from "@/features/admin/useAdminRead";
import { adminDate } from "@/features/admin/display";
import { adminBtnPrimary, adminBtnSecondary, adminInput } from "@/features/admin/ui";
import type { AdminIncidentsResponse, AdminAttentionIssue } from "@/entities/admin/types";

const panel = "rounded-2xl border border-gray-200 bg-white p-5 dark:border-zinc-700 dark:bg-zinc-800";
const statusLabels: Record<string, string> = { open: "Открыт", ack: "В работе", resolved: "Закрыт" };
const actions: Record<string, string> = { create: "Создан", ack: "Взят в работу", resolve: "Закрыт", note: "Комментарий", add_days: "Добавлены дни", reset: "Сброшен тестовый аккаунт", internal: "Изменена тестовая метка", set_plan_price: "Изменена цена", reset_plan_price: "Сброшена цена", set_global_discount: "Изменена скидка", ensure_access: "Внутренняя поддержка доступа" };
function issueLink(issue: AdminAttentionIssue) {
  if (issue.internal_user_id) return `/admin/users/${encodeURIComponent(issue.internal_user_id)}`;
  if (issue.kind === "payment") return "/admin/payments?attention=1";
  if (issue.kind === "server") return "/admin/servers";
  return null;
}
export default function AdminIncidentsPage() {
  const [userFilter, setUserFilter] = useState("");
  const { data, loading, error, refresh } = useAdminRead<AdminIncidentsResponse>(`/api/v1/admin/incidents?user=${encodeURIComponent(userFilter)}`);
  const [title, setTitle] = useState("");
  const [initialNote, setInitialNote] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [mutationError, setMutationError] = useState("");
  const [showClosed, setShowClosed] = useState(false);
  const pendingCreate = useRef<{ payload: string; id: string } | null>(null);
  const mutate = async (path: string, body: object) => {
    setBusy(true); setMessage(""); setMutationError("");
    const result = await adminFetch<{ duplicate?: boolean }>(path, { method: "POST", body: JSON.stringify(body) });
    if (result.ok) { setMessage(result.data.duplicate ? "Этот сигнал уже зарегистрирован." : "Журнал обновлён."); await refresh(); }
    else setMutationError(({ condition_still_present: "Сигнал остаётся активным. Закрытие возможно после устранения причины.", version_conflict: "Запись изменена другим оператором. Обновите журнал и повторите действие.", source_no_longer_present: "Сигнал уже исчез. Обновите журнал.", unauthorized: "Сессия истекла. Войдите в админку снова." } as Record<string, string>)[result.error] ?? "Не удалось сохранить. Повторите действие на этой странице.");
    setBusy(false);
    return result.ok;
  };
  const create = async (issue?: AdminAttentionIssue) => {
    const body = { title: issue?.title ?? title, note: issue ? "" : initialNote, ...(issue ? { source_key: issue.key } : {}) };
    const payload = JSON.stringify(body);
    if (pendingCreate.current?.payload !== payload) pendingCreate.current = { payload, id: crypto.randomUUID() };
    const ok = await mutate("/api/v1/admin/incidents", { ...body, request_id: pendingCreate.current.id });
    if (ok) { pendingCreate.current = null; if (!issue) { setTitle(""); setInitialNote(""); } }
  };
  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-bold">Инциденты и действия</h1><button className={adminBtnSecondary} onClick={() => void refresh()} disabled={loading || busy}>Обновить</button></div>
    <p className="text-sm text-gray-500">Работа с журналом не изменяет VPN-доступ или платежи. Связанный инцидент можно закрыть, когда исходный сигнал исчезнет.</p>
    {error && <p role="alert" className="text-red-600">{error}</p>}{mutationError && <p role="alert" className="text-red-600">{mutationError}</p>}{message && <p role="status" className="text-brand-600">{message}</p>}
    {loading && <p>Загрузка…</p>}
    {data && <>
      <p className="text-sm text-gray-500">Источник: БД и сохранённые наблюдения · получено {adminDate(data.observed_at)} МСК</p>
      <section className={panel}><h2 className="mb-3 font-semibold">Требует внимания · {data.total}</h2><p className="mb-3 text-sm text-gray-500">Сигналы помогают начать проверку и сами по себе не доказывают недоступность VPN. Показано не более 200.</p>{data.issues.length === 0 ? <p>Активных сигналов по проверяемым условиям нет.</p> : <ul className="divide-y dark:divide-zinc-700">{data.issues.map(issue => <li key={issue.key} className="flex flex-wrap items-center justify-between gap-3 py-3"><div><p className={issue.severity === "critical" ? "text-red-600" : "text-amber-700 dark:text-amber-400"}>{issue.title}</p>{issueLink(issue) && <Link className="text-sm text-brand-600 underline" href={issueLink(issue)!}>Открыть источник</Link>}</div><button className={adminBtnSecondary} disabled={busy} onClick={() => void create(issue)}>Зафиксировать инцидент</button></li>)}</ul>}</section>
      <details className={panel}><summary className="cursor-pointer font-semibold">Создать инцидент по обращению</summary><form className="mt-4 space-y-3" onSubmit={e => { e.preventDefault(); void create(); }}><input required maxLength={160} aria-label="Название инцидента" className={`${adminInput} w-full`} value={title} onChange={e => setTitle(e.target.value)} placeholder="Краткое описание проблемы" /><textarea maxLength={1000} aria-label="Описание инцидента" className={`${adminInput} w-full`} value={initialNote} onChange={e => setInitialNote(e.target.value)} placeholder="Что наблюдалось и что уже проверили" /><button className={adminBtnPrimary} disabled={busy || !title.trim()}>Создать</button></form></details>
      <section className="space-y-3"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">Журнал инцидентов · до 100 последних, открытые первыми</h2><label className="flex gap-2 text-sm"><input type="checkbox" checked={showClosed} onChange={e => setShowClosed(e.target.checked)} />Показать закрытые</label></div>
        {data.incidents.filter(i => showClosed || i.status !== "resolved").map(incident => <article key={incident.id} className={panel}><div className="flex flex-wrap justify-between gap-2"><h3 className="font-semibold">{incident.title}</h3><span className="text-sm text-gray-500">{statusLabels[incident.status]}</span></div><p className="mt-1 text-xs text-gray-500">{adminDate(incident.created_at)} МСК · {incident.created_by}</p>
          <details className="mt-3"><summary className="cursor-pointer text-sm text-brand-600">История · последние 50 событий</summary><ul className="mt-2 space-y-2">{data.events.filter(e => e.incident_id === incident.id).map(event => <li key={event.id} className="text-sm"><p className="text-gray-500">{adminDate(event.created_at)} МСК · {event.actor} · {actions[event.action] ?? event.action}</p><p className="whitespace-pre-wrap break-words">{event.note}</p></li>)}</ul></details>
          <textarea aria-label={`Комментарий: ${incident.title}`} maxLength={1000} className={`${adminInput} mt-3 w-full`} value={notes[incident.id] ?? ""} onChange={e => setNotes(prev => ({ ...prev, [incident.id]: e.target.value }))} placeholder="Комментарий или результат решения (обязательно для закрытия)" />
          <div className="mt-3 flex flex-wrap gap-2">{[...(incident.status === "open" ? ["ack"] : []), ...(incident.status !== "resolved" ? ["resolve"] : []), "note"].map(action => <button key={action} className={adminBtnSecondary} disabled={busy || (action !== "ack" && !(notes[incident.id] ?? "").trim())} onClick={async () => { if (await mutate(`/api/v1/admin/incidents/${incident.id}`, { action, version: incident.version, note: notes[incident.id] ?? "" })) setNotes(prev => ({ ...prev, [incident.id]: "" })); }}>{action === "ack" ? "Взять в работу" : action === "resolve" ? "Закрыть с результатом" : "Добавить комментарий"}</button>)}</div>
        </article>)}
        {data.incidents.filter(i => showClosed || i.status !== "resolved").length === 0 && <p className="text-sm text-gray-500">Записей в выбранном состоянии нет.</p>}
      </section>
      <section className={panel}><h2 className="mb-3 font-semibold">Журнал действий · последние 100 событий</h2><input className={adminInput} aria-label="Внутренний ID для журнала" value={userFilter} onChange={e => setUserFilter(e.target.value)} placeholder="Внутренний ID пользователя" /><p className="mt-2 text-xs text-gray-500">Продления, сбросы тестовых аккаунтов, тестовые метки, тарифы и внутренняя поддержка доступа. Внутренний аудит не хранит целевого пользователя.</p><ul className="mt-4 divide-y dark:divide-zinc-700">{data.audit.map(event => <li key={event.id} className="py-3 text-sm"><p>{actions[event.action] ?? event.action} · {event.detail}</p><p className="mt-1 text-xs text-gray-500">{adminDate(event.created_at)} МСК · {event.actor}</p>{event.internal_user_id && <Link className="break-all text-brand-600 underline" href={`/admin/users/${encodeURIComponent(event.internal_user_id)}`}>{event.internal_user_id}</Link>}</li>)}</ul>{data.audit.length === 0 && <p className="mt-3 text-sm text-gray-500">Событий по выбранному условию нет.</p>}</section>
    </>}
  </div>;
}
