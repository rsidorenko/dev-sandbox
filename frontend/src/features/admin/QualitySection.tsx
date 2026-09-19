"use client";

/**
 * Секция «Качество и данные» на /admin/stats: data health (достоверность
 * срезов), недельные когорты триалов и качество VPN по проверкам /check.
 * Чистая презентация типизированных данных из AdminStatsResponse.
 */

import type {
  AdminDataHealth,
  AdminNotificationHoldout,
  AdminTrialCohort,
  AdminVpnQuality,
} from "@/entities/admin/types";
import { adminNum } from "@/features/admin/ui";

const CONNECTION_REASON_LABELS: Record<string, string> = {
  install_failed: "Не установилось приложение",
  import_failed: "Не добавилась подписка",
  connect_failed: "VPN не подключается",
  traffic_failed: "Нет интернета после подключения",
  too_hard: "Слишком сложно",
  other: "Другая причина",
};

function formatRate(rate: number | null): string {
  return rate == null ? "" : ` (${Math.round(rate * 1000) / 10}%)`;
}

function formatCalendarDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  const dateValue = new Date(year, month - 1, day);
  if (Number.isNaN(dateValue.getTime())) return value;
  return dateValue.toLocaleDateString("ru-RU", { day: "2-digit", month: "long", year: "numeric" });
}

export function QualitySection({
  dataHealth,
  trialCohorts,
  vpnQuality,
  notificationHoldout,
}: {
  dataHealth?: AdminDataHealth;
  trialCohorts: AdminTrialCohort[];
  vpnQuality?: AdminVpnQuality;
  notificationHoldout?: AdminNotificationHoldout;
}) {
  return (
    <section className="mt-6 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
      <div className="border-b border-gray-200 px-5 py-4 dark:border-zinc-700">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Качество и данные</h2>
        <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
          Достоверность срезов · недельные когорты триалов · качество VPN по проверкам /check
        </p>
      </div>

      {dataHealth && (
        <div className="border-b border-gray-200 p-5 dark:border-zinc-700">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Качество данных</h3>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="overflow-hidden rounded-xl border border-gray-200 dark:border-zinc-700">
              <div className="bg-gray-50 px-4 py-3 text-xs font-medium text-gray-700 dark:bg-zinc-900/50 dark:text-gray-300">
                Сбор событий: источник · собирается с · строк
              </div>
              <div className="max-h-64 divide-y divide-gray-100 overflow-y-auto dark:divide-zinc-700">
                {dataHealth.sources.map((source) => (
                  <div
                    key={`${source.source}:${source.detail}`}
                    className="flex items-center justify-between gap-2 px-4 py-2 text-xs"
                  >
                    <span className="font-mono text-gray-600 dark:text-gray-400">
                      {source.source}/{source.detail}
                    </span>
                    <span className={`whitespace-nowrap text-gray-900 dark:text-gray-100 ${adminNum}`}>
                      {source.first_occurred_at ? source.first_occurred_at.slice(0, 10) : "—"} · {source.rows}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <div className="space-y-4">
              {dataHealth.campaigns_missing_spend.length > 0 && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs dark:border-amber-900/60 dark:bg-amber-900/20">
                  <div className="font-medium text-amber-800 dark:text-amber-300">
                    Расходы не внесены — CAC/ROAS по этим кампаниям не посчитать
                  </div>
                  <div className={`mt-1 text-amber-700 dark:text-amber-400 ${adminNum}`}>
                    {dataHealth.campaigns_missing_spend
                      .map((campaign) => `${campaign.campaign_key}: ${campaign.attributed_users} польз.`)
                      .join(" · ")}
                  </div>
                </div>
              )}
              <div className="rounded-xl border border-gray-200 px-4 py-3 text-xs dark:border-zinc-700">
                <div className="font-medium text-gray-700 dark:text-gray-300">Источники атрибуции за период</div>
                {dataHealth.attribution_sources.length ? (
                  <div className={`mt-1 text-gray-600 dark:text-gray-400 ${adminNum}`}>
                    {dataHealth.attribution_sources
                      .map(
                        (source) =>
                          `${source.campaign_key}${source.excluded_from_analytics ? " (исключён)" : ""}: ${source.attributed_users}`,
                      )
                      .join(" · ")}
                  </div>
                ) : (
                  <div className="mt-1 text-gray-400">Нет атрибуций за период</div>
                )}
              </div>
              <div className="rounded-xl border border-gray-200 px-4 py-3 text-xs dark:border-zinc-700">
                <div className="font-medium text-gray-700 dark:text-gray-300">
                  Несогласованные цепочки (триалы периода)
                </div>
                <div className={`mt-1 text-gray-600 dark:text-gray-400 ${adminNum}`}>
                  {[
                    ["без шага подключения", dataHealth.chain_anomalies.trials_without_setup],
                    ["значимый трафик без маршрута", dataHealth.chain_anomalies.meaningful_without_route],
                    ["трафик раньше шага подключения", dataHealth.chain_anomalies.traffic_before_setup],
                  ]
                    .map(([label, value]) => `${label}: ${value}`)
                    .join(" · ")}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {trialCohorts.length > 0 && (
        <div className="border-b border-gray-200 p-5 dark:border-zinc-700">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Когорты триалов по неделям</h3>
          <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
            Значимый трафик ≥1 МиБ · D1–D3 — только полностью завершённые дни · оплата ≤10 дней от старта триала
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-xs">
              <thead className="text-gray-500 dark:text-gray-400">
                <tr>
                  {["Неделя", "Триалы", "Значимый трафик", "D1", "D2", "D3", "Checkout", "Зрелые", "Оплатили ≤10д"].map(
                    (label) => (
                      <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">
                        {label}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-zinc-700">
                {trialCohorts.map((cohort) => (
                  <tr key={cohort.cohort_week} className="text-gray-700 dark:text-gray-300">
                    <td className="whitespace-nowrap px-3 py-2 font-medium text-gray-900 dark:text-gray-100">
                      с {formatCalendarDate(cohort.cohort_week)}
                    </td>
                    <td className={`px-3 py-2 ${adminNum}`}>{cohort.trials}</td>
                    <td className={`px-3 py-2 ${adminNum}`}>
                      {cohort.meaningful_users}
                      {formatRate(cohort.meaningful_rate)}
                    </td>
                    {([cohort.d1, cohort.d2, cohort.d3] as const).map((retention, index) => (
                      <td key={index} className={`px-3 py-2 ${adminNum}`}>
                        {retention.retained_users}/{retention.eligible_users}
                        {formatRate(retention.rate)}
                      </td>
                    ))}
                    <td className={`px-3 py-2 ${adminNum}`}>{cohort.checkout_users}</td>
                    <td className={`px-3 py-2 ${adminNum}`}>{cohort.mature_users}</td>
                    <td className={`px-3 py-2 ${adminNum}`}>
                      {cohort.paid_users}
                      {formatRate(cohort.paid_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {vpnQuality && (
        <div className="p-5">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Качество VPN</h3>
          <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
            {vpnQuality.overall.checks > 0
              ? `Проверок: ${vpnQuality.overall.checks} · подключено ${
                  Math.round((vpnQuality.overall.connected_rate ?? 0) * 1000) / 10
                }% · задержка p50 ${vpnQuality.overall.latency_p50_ms ?? "—"} мс / p95 ${
                  vpnQuality.overall.latency_p95_ms ?? "—"
                } мс`
              : "Проверок /check за период не было"}
          </p>
          {vpnQuality.servers.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[800px] text-left text-xs">
                <thead className="text-gray-500 dark:text-gray-400">
                  <tr>
                    {["Сервер", "Проверки", "Подключено", "Пользователи", "Задержка p50", "Задержка p95", "Скорость p50"].map(
                      (label) => (
                        <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">
                          {label}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-zinc-700">
                  {vpnQuality.servers.map((server) => (
                    <tr key={server.server_label} className="text-gray-700 dark:text-gray-300">
                      <td className="whitespace-nowrap px-3 py-2 font-medium text-gray-900 dark:text-gray-100">
                        {server.server_label}
                      </td>
                      <td className={`px-3 py-2 ${adminNum}`}>{server.checks}</td>
                      <td className={`px-3 py-2 ${adminNum}`}>
                        {server.connected_checks}
                        {formatRate(server.connected_rate)}
                      </td>
                      <td className={`px-3 py-2 ${adminNum}`}>{server.users}</td>
                      <td className={`px-3 py-2 ${adminNum}`}>{server.latency_p50_ms ?? "—"}</td>
                      <td className={`px-3 py-2 ${adminNum}`}>{server.latency_p95_ms ?? "—"}</td>
                      <td className={`px-3 py-2 ${adminNum}`}>
                        {server.throughput_p50_kbps == null
                          ? "—"
                          : `${Math.round(server.throughput_p50_kbps / 100) / 10} Мбит/с`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {(vpnQuality.web_verdicts.confirmed_users > 0 || vpnQuality.web_verdicts.problem_users > 0) && (
            <p className={`mt-3 text-xs text-gray-500 dark:text-gray-400 ${adminNum}`}>
              Вердикты с сайта: «всё работает» — {vpnQuality.web_verdicts.confirmed_users} · «есть проблема» —{" "}
              {vpnQuality.web_verdicts.problem_users}
              {vpnQuality.web_problem_reasons.length > 0 &&
                ` · причины: ${vpnQuality.web_problem_reasons
                  .map((reason) => `${CONNECTION_REASON_LABELS[reason.code] ?? reason.code}: ${reason.users}`)
                  .join(", ")}`}
            </p>
          )}
        </div>
      )}

      {notificationHoldout && Object.keys(notificationHoldout).length > 0 && (
        <div className="border-t border-gray-200 p-5 dark:border-zinc-700">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Эффект уведомлений (holdout)</h3>
          <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
            Контрольная группа не получает nudge-уведомление · конверсия = оплата в течение 7 дней после даты
            уведомления · не судите по маленьким выборкам
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="text-gray-500 dark:text-gray-400">
                <tr>
                  {["Уведомление", "Доставлено", "Оплатили", "Holdout", "Оплатили", "Разница"].map((label) => (
                    <th key={label} className="whitespace-nowrap px-3 py-2 font-medium">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-zinc-700">
                {Object.entries(notificationHoldout).map(([type, buckets]) => {
                  const delivered = buckets.delivered;
                  const holdout = buckets.holdout;
                  const lift =
                    delivered.paid_rate != null && holdout.paid_rate != null
                      ? Math.round((delivered.paid_rate - holdout.paid_rate) * 1000) / 10
                      : null;
                  return (
                    <tr key={type} className="text-gray-700 dark:text-gray-300">
                      <td className="whitespace-nowrap px-3 py-2 font-mono font-medium text-gray-900 dark:text-gray-100">
                        {type}
                      </td>
                      <td className={`px-3 py-2 ${adminNum}`}>{delivered.users}</td>
                      <td className={`px-3 py-2 ${adminNum}`}>
                        {delivered.paid_users}
                        {delivered.paid_rate != null && ` (${Math.round(delivered.paid_rate * 1000) / 10}%)`}
                      </td>
                      <td className={`px-3 py-2 ${adminNum}`}>{holdout.users}</td>
                      <td className={`px-3 py-2 ${adminNum}`}>
                        {holdout.paid_users}
                        {holdout.paid_rate != null && ` (${Math.round(holdout.paid_rate * 1000) / 10}%)`}
                      </td>
                      <td className={`px-3 py-2 ${adminNum}`}>{lift == null ? "—" : `${lift > 0 ? "+" : ""}${lift} п.п.`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
