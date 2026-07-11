/**
 * Чистая логика расчёта стоимости оформления подписки.
 * Извлечена из app/(protected)/payment/[planId]/page.tsx ради unit-тестов.
 */
import { siteConfig } from "@/shared/config/site";
import { planName } from "@/entities/tariff/helpers";

/** Цена каждого дополнительного устройства в пересчёте на месяц (₽). */
export const EXTRA_DEVICE_PRICE = 80;
/** Базовое (включённое в тариф) количество устройств. */
export const DEFAULT_DEVICES = 5;
/** Стоимость одного дня для «своего тарифа» (₽). */
export const CUSTOM_PRICE_PER_DAY = siteConfig.customPricePerDay;

export interface TariffInfo {
  label: string;
  durationDays: number;
  basePrice: number;
}

/**
 * Стоимость доп. устройств: extraDevices × цена одного устройства за весь
 * период тарифа (EXTRA_DEVICE_PRICE пропорционально дням), округлённо до рублей.
 */
export function calcExtraDeviceCost(
  durationDays: number,
  extraDevices: number,
): number {
  return extraDevices * Math.round((EXTRA_DEVICE_PRICE / 30) * durationDays);
}

/**
 * Параметры тарифа по id:
 * — фиксированный (1d/7d/…/365d) — из siteConfig.tariffs;
 * — custom:N — N дней × CUSTOM_PRICE_PER_DAY, подпись через planName();
 * — иначе null (тариф не найден).
 */
export function getTariffInfo(planId: string): TariffInfo | null {
  const tariff = siteConfig.tariffs.find((t) => t.id === planId);
  if (tariff) {
    return {
      label: tariff.label,
      durationDays: tariff.durationDays,
      basePrice: tariff.price,
    };
  }
  const match = planId.match(/^custom:(\d+)$/);
  if (match) {
    const days = parseInt(match[1], 10);
    return {
      label: planName(planId),
      durationDays: days,
      basePrice: days * CUSTOM_PRICE_PER_DAY,
    };
  }
  return null;
}
