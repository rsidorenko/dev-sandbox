export interface SubscriptionActionResponse {
  ok: boolean;
  active_until?: string;
  plan_id?: string;
  device_count?: number;
  state?: string;
}
