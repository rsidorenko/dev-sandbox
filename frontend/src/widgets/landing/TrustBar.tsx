import { siteConfig } from "@/shared/config/site";

/**
 * Узкая полоса доверия под hero: ключевые факторы принятия решения.
 * Значения берутся из siteConfig (trialDays, refundDays) — единый источник правды.
 */
export function TrustBar() {
  const items = [
    `🎁 ${siteConfig.trialDays} дня бесплатно`,
    `↩ Возврат ${siteConfig.refundDays} дней`,
    "💳 Карта и СБП",
    "💬 Поддержка в Telegram",
  ];

  return (
    <section aria-label="Преимущества" className="border-b border-gray-100 bg-white dark:border-zinc-700 dark:bg-zinc-900">
      <div className="mx-auto max-w-6xl px-4">
        <ul className="flex flex-col items-center divide-y divide-gray-100 py-3 text-center text-xs font-medium text-gray-600 sm:flex-row sm:divide-x sm:divide-y-0 sm:py-4 sm:text-sm dark:divide-zinc-700 dark:text-gray-300">
          {items.map((item) => (
            <li key={item} className="flex w-full items-center justify-center px-4 py-2 sm:w-auto">
              {item}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
