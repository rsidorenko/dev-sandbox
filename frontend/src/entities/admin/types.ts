// Типы ответов админ-API (/api/v1/admin/*).

export interface AdminUser {
  internal_user_id: string;
  telegram_user_id: number;
  is_web: boolean;
  trial_used: boolean;
  is_internal: boolean;
  subscription_state: string | null;
  is_active: boolean;
  active_until: string | null;
  remaining_days: number | null;
  plan_id: string | null;
  device_count: number | null;
  balance_rubles: number;
}

export interface AdminUsersResponse {
  facets: Record<string, { key: string; count: number }[]>;
  observed_at: string;
  users: AdminUser[];
  limit: number;
  offset: number;
  total: number;
}

export interface AdminAddDaysResponse {
  user: AdminUser;
  /** true — подписка создана «с нуля» (custom:N, 5 устройств); false — продлена/реактивирована. */
  created: boolean;
  days_added: number;
  old_active_until: string | null;
  new_active_until: string;
  plan_unchanged: string | null;
  device_count_unchanged: number | null;
  provisioning: string;
  servers_ok: number;
  correlation_id: string;
}

export interface AdminTestUserResetResponse {
  telegram_user_id: number;
  internal_user_id: string;
  vpn_outcome: "success" | "not_found";
  deleted_rows: Record<string, number>;
  correlation_id: string;
}

export type PromoAudience = "new_users" | "existing_users" | "all_users";
export type PromoDiscountKind = "percent" | "fixed_rubles";

export interface AdminPromocode {
  code: string;
  audience: PromoAudience;
  discount_kind: PromoDiscountKind;
  discount_value: number;
  /** Per-tariff скидки {plan_id: value}; null — плоская мода (discount_value). */
  discounts_by_plan: Record<string, number> | null;
  max_redemptions: number | null;
  /** Лимит активаций одним пользователем: null — неограничено, 1 — единожды. */
  max_redemptions_per_user: number | null;
  redemptions_count: number;
  expires_at: string | null;
  is_active: boolean;
  note: string | null;
  created_at: string | null;
  updated_at: string | null;
  created_by: string | null;
}

export interface AdminPromocodesResponse {
  promocodes: AdminPromocode[];
  limit: number;
  offset: number;
}

export interface AdminPromocodePayload {
  code: string;
  audience: PromoAudience;
  discount_kind: PromoDiscountKind;
  /** Плоская мода. mutually exclusive с discounts_by_plan. */
  discount_value?: number | null;
  /** Per-tariff мода {plan_id: value}. mutually exclusive с discount_value. */
  discounts_by_plan?: Record<string, number> | null;
  max_redemptions?: number | null;
  /** Лимит активаций одним пользователем: null — неограничено, 1 — единожды. */
  max_redemptions_per_user?: number | null;
  expires_at?: string | null;
  note?: string | null;
  is_active?: boolean;
}

// /api/v1/admin/plans — вкладка «Тарифы»: цены 7 планов + глобальная скидка.

export interface AdminPlanPrice {
  plan_id: string;
  duration_days: number;
  /** Код-дефолт из domain/plans.py (не редактируется). */
  default_price_rubles: number;
  /** Эффективная цена (оверрайд или дефолт). */
  price_rubles: number;
  is_overridden: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

export interface AdminGlobalDiscount {
  is_active: boolean;
  /** percent | fixed_rubles; null — выключенная скидка без сохранённых параметров. */
  discount_kind: PromoDiscountKind | null;
  /** При is_active=false может быть null. */
  discount_value: number | null;
  public_label: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export interface AdminPlansResponse {
  plans: AdminPlanPrice[];
  global_discount: AdminGlobalDiscount;
}

export interface AdminGlobalDiscountPayload {
  is_active: boolean;
  discount_kind?: PromoDiscountKind | null;
  discount_value?: number | null;
  public_label?: string | null;
}

// /api/v1/admin/stats — агрегированный дашборд статистики за период.

export type AdminStatsPeriod = "1d" | "7d" | "30d" | "90d" | "all" | "custom";

export interface AdminStatsPeriodMeta {
  key: AdminStatsPeriod;
  /** ISO (UTC) — старт окна. */
  from: string;
  /** ISO (UTC) — исключающая верхняя граница окна. */
  to: string;
  /** Включительная начальная дата в выбранной timezone. */
  date_from: string;
  /** Включительная конечная дата в выбранной timezone. */
  date_to: string;
  /** Бакет группировки рядов: hour | day | month. */
  bucket: "hour" | "day" | "month";
}

export interface AdminStatsKpi {
  revenue_rubles: number;
  payments_count: number;
  paying_users: number;
  new_users: number;
  trials_started: number;
  /** Текущий живой gauge, не за период. */
  active_subscriptions: number;
  /** Доля триалов за период, дошедших до оплаты; null — если триалов не было. */
  trial_to_paid_rate: number | null;
  /** Трафик bare_xray за период (ГБ, up+down). */
  traffic_gb: number;
  /** Максимум уникальных пользователей в сутки за период. */
  traffic_users: number;
}

export interface AdminStatsRevenuePoint {
  /** ISO локального бакета (без зоны); формат фронт берёт из period.bucket. */
  bucket: string;
  rubles: number;
  count: number;
}

export interface AdminStatsCountPoint {
  bucket: string;
  count: number;
}

export interface AdminStatsTrafficPoint {
  /** stat_date — DATE в MSK (день, независимо от period.bucket). */
  bucket: string;
  gb: number;
  users: number;
}

export interface AdminStatsTrafficByServer {
  /** label из vpn_servers; для неизвестных id — «server <id>». */
  label: string;
  gb: number;
  users: number;
}

export interface AdminStatsByPlan {
  plan_id: string;
  count: number;
}

export interface AdminProductFunnel {
  registered_users: number;
  trial_started_users: number;
  setup_started_users: number;
  platform_selected_users: number;
  connection_confirmed_users: number;
  checkout_opened_users: number;
  payment_created_users: number;
  payment_succeeded_users: number;
}

export interface AdminFunnelDiagnosticsRetention {
  eligible_users: number;
  retained_users: number;
  rate: number | null;
}

export interface AdminFunnelDiagnosticsRoute {
  started_users: number;
  meaningful_traffic_users: number;
  meaningful_traffic_rate: number | null;
}

export interface AdminFunnelDiagnosticsV2 {
  meaningful_traffic_threshold_bytes: number;
  trials: number;
  first_traffic_users: number;
  meaningful_traffic_users: number;
  trial_to_meaningful_traffic_rate: number | null;
  retention: {
    d1: AdminFunnelDiagnosticsRetention;
    d2: AdminFunnelDiagnosticsRetention;
    d3: AdminFunnelDiagnosticsRetention;
  };
  routes: {
    direct: AdminFunnelDiagnosticsRoute;
    guided: AdminFunnelDiagnosticsRoute;
    guided_after_direct_users: number;
    same_day_ambiguous_users: number;
  };
}

export interface AdminTrialFeedbackItem {
  code: string;
  users: number;
}

export interface AdminTrialFeedbackBucket {
  respondents: number;
  reasons: AdminTrialFeedbackItem[];
  platforms: AdminTrialFeedbackItem[];
  routes: AdminTrialFeedbackItem[];
  surfaces: AdminTrialFeedbackItem[];
}

export interface AdminTrialFeedback {
  connection_problem: AdminTrialFeedbackBucket;
  trial_nonpayment: AdminTrialFeedbackBucket;
}

export interface AdminExperimentResult {
  experiment_key: string;
  variant: string;
  exposures: number;
  mature_exposures: number;
  pending_exposures: number;
  payment_created_users: number;
  paid_users: number;
  meaningful_traffic_users: number;
  revenue_rubles: number;
  checkout_rate: number | null;
  paid_rate: number | null;
  meaningful_traffic_rate: number | null;
}

export interface AdminExperimentRuntime {
  experiment_key: string;
  kind: "ab" | "holdout";
  status: "running" | "paused" | "misconfigured";
  variants: string[];
  allocation: string;
  primary_metric: string;
}

// --- Качество данных / когорты / качество VPN (admin-quality-dashboard) ------

export interface AdminDataHealthSource {
  source: string;
  detail: string;
  rows: number;
  first_occurred_at: string | null;
}

export interface AdminAttributionSource {
  campaign_key: string;
  attributed_users: number;
  excluded_from_analytics: boolean;
}

export interface AdminCampaignMissingSpend {
  campaign_key: string;
  attributed_users: number;
}

export interface AdminDataHealth {
  sources: AdminDataHealthSource[];
  attribution_sources: AdminAttributionSource[];
  campaigns_missing_spend: AdminCampaignMissingSpend[];
  chain_anomalies: {
    trials: number;
    meaningful_without_route: number;
    traffic_before_setup: number;
    trials_without_setup: number;
  };
}

export interface AdminTrialCohortRetention {
  eligible_users: number;
  retained_users: number;
  rate: number | null;
}

export interface AdminTrialCohort {
  cohort_week: string;
  trials: number;
  meaningful_users: number;
  meaningful_rate: number | null;
  d1: AdminTrialCohortRetention;
  d2: AdminTrialCohortRetention;
  d3: AdminTrialCohortRetention;
  checkout_users: number;
  mature_users: number;
  paid_users: number;
  paid_rate: number | null;
}

export interface AdminVpnQualityServer {
  server_label: string;
  checks: number;
  connected_checks: number;
  connected_rate: number | null;
  users: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  throughput_p50_kbps: number | null;
}

export interface AdminVpnQuality {
  servers: AdminVpnQualityServer[];
  overall: {
    checks: number;
    connected_checks: number;
    connected_rate: number | null;
    connected_users: number;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
  };
  web_verdicts: {
    confirmed_users: number;
    problem_users: number;
  };
  web_problem_reasons: AdminTrialFeedbackItem[];
}

export interface AdminNotificationHoldoutBucket {
  /** Зрелые наблюдения с полным 7-дневным окном; знаменатель paid_rate. */
  users: number;
  total_users: number;
  pending_users: number;
  paid_users: number;
  paid_rate: number | null;
}

/** Тип → { delivered, holdout }: эффект уведомления против контрольной группы. */
export type AdminNotificationHoldout = Record<
  string,
  { delivered: AdminNotificationHoldoutBucket; holdout: AdminNotificationHoldoutBucket }
>;

export interface AdminTelegramAdsCampaign {
  campaign_key: string;
  attributed_users: number;
  trials_started: number;
  setup_started_users: number;
  platform_selected_users: number;
  first_vpn_traffic_users: number;
  meaningful_vpn_traffic_users: number;
  connection_confirmed_users: number;
  connection_problem_users: number;
  checkout_opened_users: number;
  payment_created_users: number;
  mature_trials: number;
  pending_trials: number;
  first_payment_users: number;
  second_payment_users: number;
  /** Клиенты с глобально первой оплатой в 10-дневном окне после триала. */
  acquired_users: number;
  spend_rubles: number;
  revenue_rubles: number;
  cost_per_attributed_user_rubles: number | null;
  cost_per_trial_rubles: number | null;
  cac_rubles: number | null;
  /** Выручка / расход, коэффициент x. */
  roas: number | null;
  trial_activation_rate: number | null;
  trial_to_first_payment_rate: number | null;
  platform_users: AdminTelegramAdsPlatformUsers;
}

export interface AdminTelegramAdsPlatformUsers {
  ios: number;
  android: number;
  mac: number;
  win: number;
  tv: number;
}

export interface AdminTelegramAdsFunnel {
  attributed_users: number;
  trials_started: number;
  setup_started_users: number;
  platform_selected_users: number;
  first_vpn_traffic_users: number;
  meaningful_vpn_traffic_users: number;
  connection_confirmed_users: number;
  connection_problem_users: number;
  checkout_opened_users: number;
  payment_created_users: number;
  mature_trials: number;
  pending_trials: number;
  first_payment_users: number;
  second_payment_users: number;
  acquired_users: number;
  platform_users: AdminTelegramAdsPlatformUsers;
}

export interface AdminTelegramAdsEconomics {
  spend_rubles: number;
  revenue_rubles: number;
  attributed_users: number;
  trials_started: number;
  acquired_users: number;
  cost_per_attributed_user_rubles: number | null;
  cost_per_trial_rubles: number | null;
  cac_rubles: number | null;
  roas: number | null;
}

export interface AdminTelegramAdsSpend {
  campaign_key: string;
  spend_date: string;
  amount_kopecks: number;
  amount_rubles: string;
  updated_by: string;
  updated_at: string | null;
  changed: boolean;
}

export interface AdminStatsResponse {
  ok: true;
  funnel?: {
    window_days: number;
    trials: number;
    mature_trials: number;
    pending_trials: number;
    converted_trials: number;
    trial_to_first_payment_rate: number | null;
  };
  telegram_ads?: {
    campaigns: AdminTelegramAdsCampaign[];
    economics: AdminTelegramAdsEconomics;
    funnel: AdminTelegramAdsFunnel;
    total_campaigns: number;
    truncated: boolean;
  };
  product_funnel?: AdminProductFunnel;
  funnel_diagnostics_v2?: AdminFunnelDiagnosticsV2;
  trial_feedback?: AdminTrialFeedback;
  experiments?: AdminExperimentResult[];
  experiment_runtime?: AdminExperimentRuntime[];
  data_health?: AdminDataHealth;
  trial_cohorts?: AdminTrialCohort[];
  vpn_quality?: AdminVpnQuality;
  notification_holdout?: AdminNotificationHoldout;
  period: AdminStatsPeriodMeta;
  kpi: AdminStatsKpi;
  series: {
    revenue: AdminStatsRevenuePoint[];
    new_users: AdminStatsCountPoint[];
    trials: AdminStatsCountPoint[];
    traffic: AdminStatsTrafficPoint[];
  };
  traffic_by_server: AdminStatsTrafficByServer[];
  by_plan: AdminStatsByPlan[];
}

// --- Рассылка (/api/v1/admin/broadcast/*) ---------------------------------

/** Ключи категорий аудитории (множественный выбор → UNION на бэке). */
export type BroadcastCategory =
  | "all"
  | "active"
  | "trial_active"
  | "trial_expired_no_purchase"
  | "expired_0_2d"
  | "expired_3_5d"
  | "expired_6_7d"
  | "expired_8_30d"
  | "expired_30plus"
  | "dormant";

/** Человекочитаемые подписи категорий (порядок вывода в UI). */
export const BROADCAST_CATEGORY_LABELS: { key: BroadcastCategory; label: string }[] = [
  { key: "all", label: "Все пользователи" },
  { key: "active", label: "Активные (платные + триал)" },
  { key: "trial_active", label: "Триал активен" },
  { key: "trial_expired_no_purchase", label: "Триал закончился, не купили" },
  { key: "dormant", label: "Не активировали триал (спящие)" },
  { key: "expired_0_2d", label: "Подписка истекла 0–2 дня назад" },
  { key: "expired_3_5d", label: "Подписка истекла 3–5 дней назад" },
  { key: "expired_6_7d", label: "Подписка истекла 6–7 дней назад" },
  { key: "expired_8_30d", label: "Подписка истекла 8–30 дней назад" },
  { key: "expired_30plus", label: "Подписка истекла более 30 дней назад" },
];

export interface BroadcastEstimate {
  total: number;
  per_category_counts: Partial<Record<BroadcastCategory, number>>;
  explicit_resolved: number[];
  explicit_unresolved: string[];
}

export type BroadcastJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "interrupted"
  | "cancelled";

export interface BroadcastJob {
  job_id: string;
  created_at: string | null;
  created_by: string;
  status: BroadcastJobStatus;
  categories: BroadcastCategory[];
  explicit_telegram_ids_count: number;
  message_text: string;
  parse_mode: string;
  buttons: BroadcastButtonView[];
  bonus: BroadcastBonusConfig | null;
  total_recipients: number;
  sent_count: number;
  failed_count: number;
  blocked_count: number;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
}

/** Кнопка задачи как хранится/возвращается (формат Telegram inline-кнопки). */
export interface BroadcastButtonView {
  text: string;
  callback_data?: string;
  url?: string;
  icon_custom_emoji_id?: string;
  style?: BroadcastButtonStyle;
}

/** Курированные действия бота (callback_data) — ключи для выбора в UI. */
export type BroadcastActionButton =
  | "buy_vpn"
  | "main_menu"
  | "enter_promo"
  | "my_subscription"
  | "help";

export const BROADCAST_ACTION_BUTTONS: { key: BroadcastActionButton; label: string }[] = [
  { key: "buy_vpn", label: "🔑 Купить VPN" },
  { key: "main_menu", label: "🏠 Главное меню" },
  { key: "enter_promo", label: "🎟 Ввести промокод" },
  { key: "my_subscription", label: "📋 Моя подписка" },
  { key: "help", label: "❓ Помощь" },
];

/** Цвет кнопки (Bot API 10.3): danger — красная, success — зелёная, primary — синяя. */
export type BroadcastButtonStyle = "danger" | "success" | "primary";

export interface BroadcastUrlButton {
  label: string;
  url: string;
  /** ID анимированного премиум-эмодзи (→ icon_custom_emoji_id): иконка перед текстом. */
  emoji_id?: string;
  style?: BroadcastButtonStyle;
}

/** Элемент action_buttons: ключ действия или объект с оформлением (иконка/цвет). */
export type BroadcastActionButtonEntry =
  | BroadcastActionButton
  | { key: BroadcastActionButton; emoji_id?: string; style?: BroadcastButtonStyle };

/** Payload кнопок для send/test. */
export interface BroadcastButtonsPayload {
  action_buttons?: BroadcastActionButtonEntry[];
  url_buttons?: BroadcastUrlButton[];
}

/** Бонус-кнопка рассылки (MVP: только бесплатные дни, type="days"; type="promo" зарезервирован
 * под следующий PR). При нажатении получателю начисляются дни подписки. */
export type BroadcastBonusConfig =
  | { type: "days"; days: number; label: string; ttl_days?: number; emoji_id?: string; style?: BroadcastButtonStyle } // дни 3..30
  | { type: "promo"; code: string; label: string; ttl_days?: number; emoji_id?: string; style?: BroadcastButtonStyle }; // авто-применение промокода

export interface BroadcastJobsResponse {
  jobs: BroadcastJob[];
  limit: number;
  offset: number;
}

export interface BroadcastSendResponse {
  job_id: string;
  total_recipients: number;
  explicit_unresolved?: string[];
  bonus_warnings?: string[]; // non-blocking подсказки админу (audience/TTL промокода)
}

export interface AdminUserDetail {
  user: AdminUser;
  promo_redemptions: { code: string; discounted_rubles: number; created_at: string | null }[];
  traffic: { monthly_total_bytes: number; per_server: { server_id: number; uplink_bytes: number; downlink_bytes: number }[] } | null;
  diagnostics: {
    observed_at: string; source: string; rotation_pending: boolean; pending_access_jobs: number; live_access: string;
    access_jobs: { status: string; attempts: number; created_at: string; updated_at: string; next_attempt_at: string }[];
    connection_checks: { connected: boolean; detected_server_label: string; latency_ms: number | null; throughput_kbps: number | null; platform: string; created_at: string }[];
    subscription_actions: { action: string; days: number; provisioning: string | null; created_at: string }[];
  };
}

export interface AdminServersResponse {
  observed_at: string; source: string; stale_after_seconds: number;
  servers: {
    id: number; label: string; country_code: string; server_host: string; server_port: number;
    transport_type: string; provider_kind: string; client_surface: string; is_active: boolean;
    health: "healthy" | "error" | "stale" | "unknown";
    checked_at: string | null; last_success_at: string | null; traffic_collected_at: string | null;
    traffic_bytes: number; traffic_keys: number;
  }[];
}

export interface AdminPaymentsResponse {
  observed_at: string; days: number; page: number; limit: number; total: number;
  payments: { row_id: string; occurred_at: string; internal_user_id: string | null; telegram_user_id: number | null;
    provider: string; payment_kind: string; amount_minor_units: number | null; currency_code: string | null;
    payment_status: string; reference: string; apply_status: string; access_status: string;
    access_attempts: number | null; access_created_at: string | null; access_next_attempt_at: string | null;
    attention_reason: string | null;
  }[];
}

export interface AdminAttentionIssue { key: string; kind: string; title: string; internal_user_id: string | null; severity: string; }
export interface AdminIncidentsResponse {
  observed_at: string; total: number; issues: AdminAttentionIssue[];
  incidents: { id: string; source_key: string | null; title: string; status: string; version: number; created_at: string; updated_at: string; created_by: string }[];
  events: { id: number; incident_id: string; actor: string; action: string; note: string; created_at: string }[];
  audit: { id: string; created_at: string; actor: string; internal_user_id: string | null; action: string; detail: string }[];
}
