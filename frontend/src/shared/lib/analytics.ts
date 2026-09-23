/**
 * Яндекс.Метрика — инициализация и отправка целей.
 *
 * По умолчанию ВЫКЛЮЧЕНА: счётчик не грузится, внешних запросов нет
 * (PD-аудит 2026-09-11 — Вебвизор без consent-баннера и упоминания в политике).
 * Включается только явным заданием NEXT_PUBLIC_YM_ID на этапе сборки; при
 * включении обязательны consent-баннер и раскрытие в политике.
 * Запись сессий (webvisor) не возвращать ни при каких настройках.
 *
 * Цели (когда включена): choose_plan, trial_click (уход в бота за триалом),
 * start_checkout, referral_open, auth_gate_email, auth_gate_bot, purchase.
 */

export const YM_ID = process.env.NEXT_PUBLIC_YM_ID || "";

declare global {
  interface Window {
    ym?: (...args: unknown[]) => void;
  }
}

/** Init-сниппет Метрики; пустая строка = счётчик выключен (ничего не рендерится). */
export function metrikaInitScript(): string {
  if (!YM_ID) return "";
  return `(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};m[i].l=1*new Date();for(var j=0;j<document.scripts.length;j++){if(document.scripts[j].src===r){return;}}k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)})(window,document,"script","https://mc.yandex.ru/metrika/tag.js?id=${YM_ID}","ym");ym(${YM_ID},"init",{ssr:true,webvisor:false,clickmap:false,ecommerce:"dataLayer",referrer:document.referrer,url:location.href,accurateTrackBounce:true,trackLinks:true});`;
}

/** URL пикселя для <noscript>; пустая строка = пиксель выключен. */
export function metrikaPixelUrl(): string {
  if (!YM_ID) return "";
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
