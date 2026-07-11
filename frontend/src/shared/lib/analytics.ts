/**
 * Яндекс.Метрика — инициализация и отправка целей.
 *
 * id счётчика 110605037 (можно переопределить через NEXT_PUBLIC_YM_ID).
 * Цели: choose_plan, trial_click, trial_activated, start_checkout,
 * referral_open, auth_gate_email, auth_gate_bot, purchase (успешная оплата).
 */

export const YM_ID = process.env.NEXT_PUBLIC_YM_ID || "110605037";

declare global {
  interface Window {
    ym?: (...args: unknown[]) => void;
  }
}

/** Init-сниппет Метрики (id в URL tag.js + опции из кабинета). */
export function metrikaInitScript(): string {
  return `(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};m[i].l=1*new Date();for(var j=0;j<document.scripts.length;j++){if(document.scripts[j].src===r){return;}}k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)})(window,document,"script","https://mc.yandex.ru/metrika/tag.js?id=${YM_ID}","ym");ym(${YM_ID},"init",{ssr:true,webvisor:true,clickmap:true,ecommerce:"dataLayer",referrer:document.referrer,url:location.href,accurateTrackBounce:true,trackLinks:true});`;
}

/** URL пикселя для <noscript>. */
export function metrikaPixelUrl(): string {
  return `https://mc.yandex.ru/watch/${YM_ID}`;
}

/** Отправить достижение цели (no-op вне браузера). */
export function reachGoal(goalId: string, params?: Record<string, unknown>): void {
  if (typeof window === "undefined" || !window.ym) return;
  try {
    window.ym(Number(YM_ID), "reachGoal", goalId, params);
  } catch {
    // Метрика не должна ломать пользовательский флоу
  }
}
