"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { AdminUserDetail } from "@/entities/admin/types";
import { adminApi } from "@/features/admin/api";
import { adminDate } from "@/features/admin/display";
import { AddDaysModal } from "@/features/admin/AddDaysModal";
import { InternalAccountModal, ResetTestAccountModal } from "@/features/admin/TestAccountModals";
import { adminBtnPrimary, adminBtnSecondary } from "@/features/admin/ui";

const panel = "rounded-2xl border border-gray-200 bg-white p-5 dark:border-zinc-700 dark:bg-zinc-800";
export default function AdminUserPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<AdminUserDetail | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<"days" | "internal" | "reset" | null>(null);
  const sequence = useRef(0);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true); setError("");
    const result = await adminApi.getUser(id);
    if (current !== sequence.current) return;
    if (!result.ok && result.error === "unauthorized") {
      window.location.href = `/admin/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
      return;
    }
    if (result.ok) setData(result.data);
    else { setData(null); setError(result.error === "user_not_found" ? "Пользователь не найден." : "Не удалось получить карточку."); }
    setLoading(false);
  }, [id]);
  useEffect(() => { const counter = sequence; void load(); return () => { counter.current++; }; }, [load]);
  useEffect(() => { setModal(null); setData(null); setNotice(""); }, [id]);
  const saved = () => { setModal(null); void load(); };
  const user = data?.user;
  const diagnostics = data?.diagnostics;
  return <div className="space-y-5">
    <Link href="/admin/users" className="text-sm text-brand-600">← Все пользователи</Link>
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-bold">Карточка пользователя {user?.telegram_user_id}</h1><button className={adminBtnSecondary} disabled={loading} onClick={() => void load()}>Обновить диагностику</button></div>
    {loading && <p role="status">Загрузка…</p>}
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {notice && <p role="status" className="text-brand-600">{notice}</p>}
    {!loading && user && diagnostics && <>
      <p className="text-sm text-gray-500">Данные БД на {adminDate(diagnostics.observed_at)} МСК. Проверка ничего не меняет в подписке или на серверах.</p>
      <div className="grid gap-4 xl:grid-cols-3">
        <section className={panel}><h2 className="mb-3 font-semibold">Контакт и подписка</h2><dl className="space-y-2 text-sm">
          <div><dt className="text-gray-500">Внутренний ID</dt><dd className="break-all font-mono">{user.internal_user_id}</dd></div>

          <div><dt className="text-gray-500">Право на VPN-доступ</dt><dd>{user.is_active ? "Активно" : "Неактивно"}</dd></div>
          <div><dt className="text-gray-500">Срок (МСК)</dt><dd>{adminDate(user.active_until)} · {user.remaining_days ?? 0} дн.</dd></div>
          <div><dt className="text-gray-500">Тариф / устройства</dt><dd>{user.plan_id ?? "Без платного тарифа"} / {user.device_count ?? "Нет данных"}</dd></div>
          <div><dt className="text-gray-500">Баланс</dt><dd>{user.balance_rubles.toFixed(2)} ₽</dd></div>
          <div><dt className="text-gray-500">Триал / аккаунт</dt><dd>{user.trial_used ? "Использован" : "Не использован"} / {user.is_internal ? "Тестовый" : "Обычный"}</dd></div>
        </dl></section>
        <section className={panel}><h2 className="mb-3 font-semibold">Диагностика доступа</h2>
          <ul className="space-y-3 text-sm">
            <li>{user.is_active ? "✓ Подписка даёт право на доступ." : "Подписка не даёт права на доступ — проверьте срок и статус."}</li>
            <li className={diagnostics.rotation_pending ? "text-amber-700 dark:text-amber-400" : ""}>{diagnostics.rotation_pending ? "Требует внимания: ротация ключей ещё не завершена." : "Незавершённой ротации нет."}</li>
            <li>{diagnostics.pending_access_jobs > 0 ? "Требует внимания: выдача после балансоплаты ожидает завершения." : "Ожидающих задач выдачи после балансоплаты нет."}</li>
            <li className="text-gray-500">Работа туннеля прямо сейчас не проверялась. Состояние подписки и выполненная задача выдачи не подтверждают доступность со стороны устройства.</li>
          </ul>
          <p className="mt-4 text-sm">Трафик за текущий месяц (МСК): {data.traffic === null ? "нет данных" : `${(data.traffic.monthly_total_bytes / 1024 ** 3).toFixed(2)} ГБ`}</p>
        </section>
        <section className={panel}><h2 className="mb-3 font-semibold">Действия поддержки</h2><button className={adminBtnPrimary} onClick={() => setModal("days")}>Добавить дни</button>
          <details className="mt-6 border-t pt-4 dark:border-zinc-700"><summary className="cursor-pointer text-sm text-gray-500">Дополнительные действия</summary><div className="mt-3 flex flex-col items-start gap-3"><button className={adminBtnSecondary} onClick={() => setModal("internal")}>{user.is_internal ? "Снять тестовую метку" : "Пометить тестовым"}</button>{user.is_internal && <button className="text-sm text-red-600" onClick={() => setModal("reset")}>Сбросить тестовый аккаунт…</button>}</div></details>
        </section>
      </div>
      <section className={panel}><h2 className="mb-3 font-semibold">Проверки с устройства · последние 20</h2><p className="mb-3 text-sm text-gray-500">История страницы проверки подключения. Результат относится к указанному моменту и сети пользователя.</p>{diagnostics.connection_checks.length === 0 ? <p>Проверок нет.</p> : <ul className="divide-y dark:divide-zinc-700">{diagnostics.connection_checks.map((check, index) => <li key={index} className="py-3 text-sm">{adminDate(check.created_at)} МСК · {check.connected ? "Подключение подтверждено" : "Подключение не подтверждено"} · {check.detected_server_label || "Сервер не определён"} · {check.platform || "Платформа неизвестна"} · {check.latency_ms ?? "—"} мс · {check.throughput_kbps ?? "—"} кбит/с</li>)}</ul>}</section>
      <div className="grid gap-4 xl:grid-cols-2">
        <section className={panel}><h2 className="mb-3 font-semibold">Выдача после балансоплаты · последние 20</h2>{diagnostics.access_jobs.length === 0 ? <p className="text-sm text-gray-500">Таких задач нет.</p> : diagnostics.access_jobs.map((job, i) => <p key={i} className="py-2 text-sm">{adminDate(job.updated_at)} МСК · {({ pending: "Ожидает", done: "Выполнена", superseded: "Больше не актуальна" })[job.status] ?? job.status} · попыток: {job.attempts}{job.status === "pending" && ` · повтор: ${adminDate(job.next_attempt_at)}`}</p>)}</section>
        <section className={panel}><h2 className="mb-3 font-semibold">Действия поддержки · последние 30</h2>{diagnostics.subscription_actions.length === 0 ? <p className="text-sm text-gray-500">Продлений оператором нет.</p> : diagnostics.subscription_actions.map((action, i) => <p key={i} className="py-2 text-sm">{adminDate(action.created_at)} МСК · добавлено {action.days} дн. · выдача: {action.provisioning ?? "нет данных"}</p>)}</section>
      </div>
      <section className={panel}><h2 className="mb-3 font-semibold">Использованные промокоды · последние 50</h2>{data.promo_redemptions.length === 0 ? <p className="text-sm text-gray-500">Использований нет.</p> : data.promo_redemptions.map((promo, i) => <p key={i} className="py-2 text-sm">{adminDate(promo.created_at)} МСК · {promo.code} · {promo.discounted_rubles} ₽</p>)}</section>

    </>}
    {user && modal === "days" && <AddDaysModal user={user} onClose={() => setModal(null)} onSaved={() => void load()} />}
    {user && modal === "internal" && <InternalAccountModal user={user} onClose={() => setModal(null)} onSaved={saved} />}
    {user && modal === "reset" && <ResetTestAccountModal user={user} onClose={() => setModal(null)} onReset={() => { setNotice("Тестовый аккаунт сброшен. Теперь отправьте боту /start."); saved(); }} />}
  </div>;
}
