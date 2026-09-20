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
import { MetricHelp } from "@/features/admin/MetricHelp";
import { adminNum } from "@/features/admin/ui";

const QUALITY_HELP = {
  root: "Техническая достоверность отчёта, поведение зрелых когорт и реальные проверки VPN. Начинайте отсюда, если бизнес-метрика выглядит неожиданно.",
  data: "Показывает полноту источников и нарушения ожидаемой последовательности событий. Аномалии могут означать баг трекинга, а не реальную проблему пользователей.",
  sources: "Дата первого доступного события и число строк за период. Метрика помогает понять, с какого момента конкретному срезу можно доверять.",
  attribution: "Количество пользователей по рекламным меткам. Исключённые источники не входят в бизнес-агрегаты и CAC/ROAS.",
  anomalies: "События, нарушающие ожидаемый порядок воронки. Рост показателя — повод проверить дедупликацию, время событий и покрытие клиентов.",
  cohorts: "Пользователи сгруппированы по неделе старта триала. D1–D3 и оплаты считаются только там, где окно уже полностью завершилось.",
  vpn: "Результаты автоматической проверки /check: доля подключений, задержка и скорость по серверам. Низкая доля подключения важнее единичного медленного замера.",
  holdout: "Сравнение пользователей, получивших маркетинговое уведомление, с контрольной группой без него. Положительная разница после созревания показывает добавочный эффект сообщения.",
} as const;

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

function QualityHeading({ title, help, level = 3 }: { title: string; help: string; level?: 2 | 3 }) {
  const titleClass = "min-w-0 break-words text-sm font-semibold text-gray-900 dark:text-gray-100";
  return (
    <div className="flex min-w-0 items-center gap-2">
      {level === 2 ? <h2 className={titleClass}>{title}</h2> : <h3 className={titleClass}>{title}</h3>}
      <MetricHelp label={title}>{help}</MetricHelp>
    </div>
  );
}

function MobileScrollHint() {
  return <p className="mt-3 text-[11px] text-gray-400 sm:hidden">Проведите по таблице влево, чтобы увидеть остальные столбцы →</p>;
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
    <section
      data-testid="quality-section"
      className="mt-6 w-full min-w-0 max-w-full overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-800"
    >
      <div className="border-b border-gray-200 px-4 py-4 dark:border-zinc-700 sm:px-5">
        <QualityHeading title="Качество и данные" help={QUALITY_HELP.root} level={2} />
        <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
          Достоверность срезов · недельные когорты триалов · качество VPN по проверкам /check
        </p>
      </div>

      {dataHealth && (
        <div className="border-b border-gray-200 p-4 dark:border-zinc-700 sm:p-5">
          <QualityHeading title="Качество данных" help={QUALITY_HELP.data} />
          <div className="mt-4 grid min-w-0 gap-4 lg:grid-cols-2">
            <div className="min-w-0 overflow-hidden rounded-xl border border-gray-200 dark:border-zinc-700">
              <div className="flex min-w-0 items-center gap-2 bg-gray-50 px-4 py-3 text-xs font-medium text-gray-700 dark:bg-zinc-900/50 dark:text-gray-300">
                <span className="min-w-0 break-words">Сбор событий: источник · собирается с · строк</span>
                <MetricHelp label="Сбор событий">{QUALITY_HELP.sources}</MetricHelp>
              </div>
              <div className="max-h-64 divide-y divide-gray-100 overflow-y-auto dark:divide-zinc-700">
                {dataHealth.sources.map((source) => (
                  <div
                    key={`${source.source}:${source.detail}`}
                    className="flex min-w-0 flex-col items-start gap-1 px-4 py-2 text-xs sm:flex-row sm:items-center sm:justify-between sm:gap-2"
                  >
                    <span className="min-w-0 max-w-full break-all font-mono text-gray-600 dark:text-gray-400">
                      {source.source}/{source.detail}
                    </span>
                    <span className={`whitespace-nowrap text-gray-900 dark:text-gray-100 ${adminNum}`}>
                      {source.first_occurred_at ? source.first_occurred_at.slice(0, 10) : "—"} · {source.rows}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <div className="min-w-0 space-y-4">
              {dataHealth.campaigns_missing_spend.length > 0 && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs dark:border-amber-900/60 dark:bg-amber-900/20">
                  <div className="font-medium text-amber-800 dark:text-amber-300">
                    Расходы не внесены — CAC/ROAS по этим кампаниям не посчитать
                  </div>
                  <div className="mt-1 whitespace-normal break-words tabular-nums text-amber-700 [overflow-wrap:anywhere] dark:text-amber-400">
                    {dataHealth.campaigns_missing_spend
                      .map((campaign) => `${campaign.campaign_key}: ${campaign.attributed_users} польз.`)
                      .join(" · ")}
                  </div>
                </div>
              )}
              <div className="rounded-xl border border-gray-200 px-4 py-3 text-xs dark:border-zinc-700">
                <div className="flex min-w-0 items-center gap-2 font-medium text-gray-700 dark:text-gray-300">
                  <span className="min-w-0 break-words">Источники атрибуции за период</span>
                  <MetricHelp label="Источники атрибуции">{QUALITY_HELP.attribution}</MetricHelp>
                </div>
                {dataHealth.attribution_sources.length ? (
                  <div className="mt-1 whitespace-normal break-words tabular-nums text-gray-600 [overflow-wrap:anywhere] dark:text-gray-400">
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
                <div className="flex min-w-0 items-center gap-2 font-medium text-gray-700 dark:text-gray-300">
                  <span className="min-w-0 break-words">Несогласованные цепочки (триалы периода)</span>
                  <MetricHelp label="Несогласованные цепочки">{QUALITY_HELP.anomalies}</MetricHelp>
                </div>
                <div className="mt-1 whitespace-normal break-words tabular-nums text-gray-600 [overflow-wrap:anywhere] dark:text-gray-400">
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
        <div className="border-b border-gray-200 p-4 dark:border-zinc-700 sm:p-5">
          <QualityHeading title="Когорты триалов по неделям" help={QUALITY_HELP.cohorts} />
          <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
            Значимый трафик ≥1 МиБ · D1–D3 — только полностью завершённые дни · оплата ≤10 дней от старта триала
          </p>
          <MobileScrollHint />
          <div className="mt-2 max-w-full touch-pan-x overflow-x-auto [-webkit-overflow-scrolling:touch] sm:mt-4">
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
        <div className="p-4 sm:p-5">
          <QualityHeading title="Качество VPN" help={QUALITY_HELP.vpn} />
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
            <>
              <MobileScrollHint />
              <div className="mt-2 max-w-full touch-pan-x overflow-x-auto [-webkit-overflow-scrolling:touch] sm:mt-4">
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
            </>
          )}
          {(vpnQuality.web_verdicts.confirmed_users > 0 || vpnQuality.web_verdicts.problem_users > 0) && (
            <p className="mt-3 whitespace-normal break-words text-xs tabular-nums text-gray-500 [overflow-wrap:anywhere] dark:text-gray-400">
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
        <div className="border-t border-gray-200 p-4 dark:border-zinc-700 sm:p-5">
          <QualityHeading title="Эффект уведомлений (holdout)" help={QUALITY_HELP.holdout} />
          <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">
            Контрольная группа не получает nudge-уведомление · конверсия = оплата в точные 7 суток после
            отправки · CR считается только по зрелым наблюдениям
          </p>
          <MobileScrollHint />
          <div className="mt-2 max-w-full touch-pan-x overflow-x-auto [-webkit-overflow-scrolling:touch] sm:mt-4">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="text-gray-500 dark:text-gray-400">
                <tr>
                  {["Уведомление", "Доставлено: зрелые/всего", "Оплатили после доставки", "Holdout: зрелые/всего", "Оплатили в holdout", "Разница"].map((label) => (
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
                      <td className={`px-3 py-2 ${adminNum}`}>
                        {delivered.users}/{delivered.total_users}
                        {delivered.pending_users > 0 && ` (${delivered.pending_users} ожидают)`}
                      </td>
                      <td className={`px-3 py-2 ${adminNum}`}>
                        {delivered.paid_users}
                        {delivered.paid_rate != null && ` (${Math.round(delivered.paid_rate * 1000) / 10}%)`}
                      </td>
                      <td className={`px-3 py-2 ${adminNum}`}>
                        {holdout.users}/{holdout.total_users}
                        {holdout.pending_users > 0 && ` (${holdout.pending_users} ожидают)`}
                      </td>
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
