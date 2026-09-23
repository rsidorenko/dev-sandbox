"use client";

import { useAdminRead } from "./useAdminRead";
import { adminDate } from "./display";
import { adminBtnSecondary, adminTable, adminTableWrap, adminTd, adminTh } from "./ui";

type Location = { location: string; enabled: boolean; active_until_utc: string | null; started_at: string | null; status: string | null; reason: string };
type Profile = { location: string; server_id: number; route: number; label: string; attempts: number; verified: number; unverified: number; measurement_errors: number; errors: number; slow: number; p50_ms: number | null; p95_ms: number | null; last_checked_at: string };
type Sample = { location: string; started_at: string; server_id: number; route: number; label: string; result: string; tls_ms: number | null; ttfb_ms: number | null };
type Response = { locations: Location[]; profiles: Profile[]; recent: Sample[]; stale_after_seconds: number };
const results: Record<string, string> = { ok: "Выход проверен", route_unknown: "Маршрут не подтверждён", route_mismatch: "Другой выход", timeout: "Таймаут", tls_error: "Ошибка TLS", connect_error: "Ошибка соединения", http_error: "Ошибка HTTP", invalid_response: "Неожиданный ответ", measurement_error: "Ошибка измерителя" };
const reasons: Record<string, string> = { disabled: "Выключено", invalid_identity: "Аккаунт не подходит", rotation_pending: "Ожидание ротации ключа", expired: "Истёк служебный доступ", inactive_keys: "Ключи неактивны", collector_failed: "Ошибка измерителя" };
const ms = (value: number | null) => value == null ? "—" : `${Math.round(value)} мс`;

export function VpnQuality() {
  const { data, loading, error, refresh } = useAdminRead<Response>("/api/v1/admin/vpn-quality");
  const stale = (at: string | null) => !at || Date.now() - Date.parse(at) > (data?.stale_after_seconds ?? 900) * 1000;
  return <section className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-800">
    <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">Качество VPN за 24 часа</h2><button className={adminBtnSecondary} disabled={loading} onClick={() => void refresh()}>Обновить качество</button></div>
    <p className="text-sm text-gray-500">HTTPS через служебный VPN-профиль к Cloudflare. TTFB включает подключение и ожидание первого байта; это не ping и не скорость скачивания. Измерения с серверной точки не отражают все сети пользователей.</p>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {loading && <p role="status">Загрузка качества…</p>}
    {data && <>
      {data.locations.length === 0 && <p>Регулярные проверки не настроены.</p>}
      {data.locations.map(l => <p key={l.location} className="text-sm"><strong>{l.location}</strong>: {!l.enabled ? "Выключено" : l.status === "skipped" ? reasons[l.reason] ?? "Проверка пропущена" : stale(l.started_at) ? "Нет свежих измерений" : l.status === "measurement_error" ? "Ошибка измерителя" : l.status === "running" ? "Идёт проверка" : "Цикл завершён"}. Последний запуск: {adminDate(l.started_at)} МСК. Служебный доступ до {adminDate(l.active_until_utc)} МСК.</p>)}
      {data.profiles.length > 0 && <div className={adminTableWrap}><table className={adminTable}><thead><tr>{["Профиль / точка", "Попытки / ошибки", "TTFB p50 / p95", "Ответы >1 с", "Свежесть"].map(h => <th className={adminTh} key={h}>{h}</th>)}</tr></thead><tbody>{data.profiles.map(p => <tr key={`${p.location}:${p.server_id}:${p.route}`}><td className={adminTd}>{p.label}<p className="text-xs text-gray-500">{p.location}{p.route ? " · автовыбор" : ""}</p></td><td className={adminTd}>{p.attempts} / {p.errors}<p className="text-xs text-gray-500">Ошибки измерителя: {p.measurement_errors}; маршрут не подтверждён: {p.unverified}</p></td><td className={adminTd}>{ms(p.p50_ms)} / {ms(p.p95_ms)}</td><td className={adminTd}>{p.slow}</td><td className={adminTd}>{stale(p.last_checked_at) ? "Устарело" : "Свежие данные"}<p className="text-xs text-gray-500">{adminDate(p.last_checked_at)} МСК</p></td></tr>)}</tbody></table></div>}
      {data.recent.length > 0 && <details><summary className="cursor-pointer font-medium">Последние результаты (до 100)</summary><div className={adminTableWrap}><table className={adminTable}><thead><tr>{["Время (МСК)", "Профиль / точка", "Результат", "TLS / TTFB"].map(h => <th className={adminTh} key={h}>{h}</th>)}</tr></thead><tbody>{data.recent.map(s => <tr key={`${s.started_at}:${s.location}:${s.server_id}:${s.route}`}><td className={adminTd}>{adminDate(s.started_at)}</td><td className={adminTd}>{s.label} · {s.location}</td><td className={adminTd}>{results[s.result] ?? "Неизвестно"}</td><td className={adminTd}>{ms(s.tls_ms)} / {ms(s.ttfb_ms)}</td></tr>)}</tbody></table></div></details>}
      <p className="text-xs text-gray-500">Для автовыбора проверяется принадлежность выхода сети, а не конкретному пулу. Ошибки измерителя учитываются отдельно от ошибок HTTPS-проверки. TLS — время до завершения HTTPS/TLS целевого сайта через VPN.</p>
    </>}
  </section>;
}
