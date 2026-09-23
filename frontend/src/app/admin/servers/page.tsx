"use client";

import { useState } from "react";
import { VpnQuality } from "@/features/admin/VpnQuality";
import { useAdminRead } from "@/features/admin/useAdminRead";
import { adminDate } from "@/features/admin/display";
import { adminBtnSecondary, adminInput, adminRow, adminTable, adminTableWrap, adminTd, adminTh } from "@/features/admin/ui";
import type { AdminServersResponse } from "@/entities/admin/types";

const states = { healthy: "Данные получены", error: "Ошибка опроса", stale: "Данные устарели", unknown: "Нет наблюдений" };
export default function AdminServersPage() {
  const { data, loading, error, refresh } = useAdminRead<AdminServersResponse>("/api/v1/admin/servers");
  const [attention, setAttention] = useState(false);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [search, setSearch] = useState("");
  const servers = (data?.servers ?? []).filter(s => (includeInactive || s.is_active) && (!attention || s.health !== "healthy") && `${s.label} ${s.server_host} ${s.transport_type}`.toLowerCase().includes(search.toLowerCase()));
  const hosts = [...new Set(servers.map(s => s.server_host))];
  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-bold">Серверы и подключения</h1><p className="mt-1 text-sm text-gray-500">{hosts.length} входных хостов · {servers.length} подключений</p></div><button className={adminBtnSecondary} onClick={() => void refresh()} disabled={loading}>Обновить</button></div>
    <VpnQuality />
    <p className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200">Проверяется получение статистики Xray. Успех опроса не гарантирует работу VPN из сети пользователя; ошибка опроса не доказывает падение сервера. TCP/XHTTP — разные подключения одного хоста.</p>
    <div className="flex flex-wrap items-center gap-4"><input aria-label="Поиск серверов" className={adminInput} value={search} onChange={e => setSearch(e.target.value)} placeholder="Страна, хост, транспорт" /><label className="flex gap-2 text-sm"><input type="checkbox" checked={attention} onChange={e => setAttention(e.target.checked)} />Требует внимания</label><label className="flex gap-2 text-sm"><input type="checkbox" checked={includeInactive} onChange={e => setIncludeInactive(e.target.checked)} />Показать отключённые</label></div>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {loading && <p role="status">Загрузка…</p>}
    {!loading && data && <>
      <p className="text-sm text-gray-500">Источник: {data.source} · получено {adminDate(data.observed_at)} МСК. Наблюдение устаревает через {Math.round(data.stale_after_seconds / 60)} мин. после опроса. Трафик — за текущие сутки МСК.</p>
      {hosts.length === 0 && <p className="rounded-xl border p-6 text-gray-500">Подключений по выбранным условиям нет.</p>}
      {hosts.map(host => <section key={host} className="space-y-2"><h2 className="font-mono font-semibold">{host}</h2><div className={adminTableWrap}><table className={adminTable}><thead><tr>{["Подключение", "В подписке", "Опрос статистики", "Последняя попытка (МСК)", "Последний успех (МСК)", "Трафик / ключи"].map(label => <th key={label} className={adminTh}>{label}</th>)}</tr></thead><tbody>{servers.filter(s => s.server_host === host).map(s => <tr key={s.id} className={adminRow}>
        <td className={adminTd}><p className="font-semibold">{s.label}</p><p className="text-xs text-gray-500">#{s.id} · {s.transport_type.toUpperCase()} · {s.provider_kind} · {s.client_surface}</p></td>
        <td className={adminTd}>{s.is_active ? "Включено" : "Отключено"}</td>
        <td className={`${adminTd} ${s.health === "healthy" ? "text-green-700 dark:text-green-400" : s.health === "error" ? "text-red-600" : "text-amber-700 dark:text-amber-400"}`}>{states[s.health]}</td>
        <td className={adminTd}>{adminDate(s.checked_at)}</td><td className={adminTd}>{adminDate(s.last_success_at)}</td>
        <td className={adminTd}>{s.traffic_collected_at ? <>{(s.traffic_bytes / 1024 ** 3).toFixed(2)} ГБ / {s.traffic_keys}<p className="text-xs text-gray-500">Сбор: {adminDate(s.traffic_collected_at)}</p></> : "Нет данных"}</td>
      </tr>)}</tbody></table></div></section>)}
      <p className="text-xs text-gray-500">Ключи в трафике — уникальные учётные записи подключения с ненулевым трафиком за день, не число устройств онлайн. Сумма по подключениям может учитывать одного пользователя несколько раз.</p>
    </>}
  </div>;
}
