"use client";

import Link from "next/link";
import { siteConfig } from "@/shared/config/site";
import { reachGoal } from "@/shared/lib/analytics";

/**
 * Полоса доверия под hero. НЕ «мёртвый текст»: каждый пункт — действие/ссылка.
 *   • пробный период → /trial
 *   • возврат        → /refund (условия)
 *   • оплата         → #tariffs (к блоку тарифов)
 *   • поддержка      → Telegram
 * Единые outline-иконки (вместо разнородных эмодзи) + hover.
 */
const ITEM =
  "group inline-flex items-center justify-center gap-2 rounded-full px-3.5 py-2 text-sm font-medium transition";
const ITEM_NEUTRAL =
  "text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-zinc-800 dark:hover:text-white";

export function TrustBar() {
  return (
    <nav
      aria-label="Гарантии и условия"
      className="border-b border-gray-100 bg-white dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="mx-auto max-w-6xl px-4">
        <ul className="flex flex-wrap items-center justify-center gap-1.5 py-2.5 sm:gap-2">
          <li>
            <Link
              href="/trial"
              onClick={() => reachGoal("trustbar_trial")}
              className={`${ITEM} bg-brand-50 text-brand-700 hover:bg-brand-100 dark:bg-brand-950/40 dark:text-brand-300 dark:hover:bg-brand-900/40`}
            >
              <GiftIcon />
              {siteConfig.trialDays} дня бесплатно
            </Link>
          </li>
          <li>
            <Link href="/refund" className={`${ITEM} ${ITEM_NEUTRAL}`}>
              <RefundIcon />
              Возврат {siteConfig.refundDays} дней
            </Link>
          </li>
          <li>
            <a
              href="#tariffs"
              onClick={() => reachGoal("trustbar_tariffs")}
              className={`${ITEM} ${ITEM_NEUTRAL}`}
            >
              <CardIcon />
              Оплата картой и СБП
            </a>
          </li>
          <li>
            <a
              href={siteConfig.supportTelegram}
              target="_blank"
              rel="noopener noreferrer"
              className={`${ITEM} ${ITEM_NEUTRAL}`}
            >
              <ChatIcon />
              Поддержка в Telegram
            </a>
          </li>
        </ul>
      </div>
    </nav>
  );
}

/* Единый стиль иконок: outline 1.5, как в FeaturesSection. */

function GiftIcon() {
  return (
    <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M21 11.25v8.25a1.5 1.5 0 0 1-1.5 1.5H5.25a1.5 1.5 0 0 1-1.5-1.5v-8.25M12 4.875A2.625 2.625 0 1 0 9.375 7.5H12m0-2.625V7.5m0-2.625A2.625 2.625 0 1 1 14.625 7.5H12m0 0V21m-8.625-9.75h18c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125h-18c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125Z"
      />
    </svg>
  );
}

function RefundIcon() {
  return (
    <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3" />
    </svg>
  );
}

function CardIcon() {
  return (
    <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3h15a2.25 2.25 0 0 0 2.25-2.25V6.75A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25v10.5A2.25 2.25 0 0 0 4.5 19.5Z"
      />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.184-4.183a1.14 1.14 0 0 1 .778-.332 48.294 48.294 0 0 0 5.83-.498c1.585-.233 2.708-1.626 2.708-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z"
      />
    </svg>
  );
}
