/** Типы публичного каталога тарифов /api/v1/plans (runtime-цены + глобальная скидка). */

export type GlobalDiscountKind = "percent" | "fixed_rubles";

/** Дескриптор активной глобальной скидки (неактивная приходит null). */
export interface GlobalDiscountDescriptor {
  discount_kind: GlobalDiscountKind;
  discount_value: number;
  /** Короткая подпись: «−20%» / «−200 ₽». */
  label: string;
  /** Публичная подпись оператора (напр. «Акция»); null — не задана. */
  public_label: string | null;
}

export interface PlansCatalogPlan {
  plan_id: string;
  duration_days: number;
  /** Эффективная цена (админ-оверрайд или код-дефолт), ₽. */
  base_rubles: number;
}

export interface PlansCatalogResponse {
  ok: true;
  plans: PlansCatalogPlan[];
  global_discount: GlobalDiscountDescriptor | null;
  custom_day_price_rubles: number;
  extra_device_price_rubles: number;
  devices: { base: number; max: number };
}
