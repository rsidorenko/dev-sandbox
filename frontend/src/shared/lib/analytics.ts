/**
 * Яндекс.Метрика — инициализация и отправка целей.
 *
 * Счётчик включается через переменную окружения NEXT_PUBLIC_YM_ID (числовой id
 * счётчика). Если переменная не задана — Метрика не подключается (безопасно для
 * dev/merge). Цели: choose_plan (CTA тарифа), start_checkout (Оформить подписку),
 * purchase (успешная оплата).
 */

export const YM_ID = process.env.NEXT_PUBLIC_YM_ID ?? "";

declare global {
  interface Window {
    ym?: (...args: unknown[]) => void;
  }
}

/** Стандартный init-сниппет Метрики. Пустая строка, если id не задан. */
export function metrikaInitScript(): string {
  if (!YM_ID) return "";
  return `(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};m[i].l=1*new Date();for(var j=0;j<document.scripts.length;j++){if(document.scripts[j].src===r){return;}}k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)})(window,document,"script","https://mc.yandex.ru/metrika/tag.js","ym");ym(${YM_ID},"init",{clickmap:true,trackLinks:true,accurateTrackBounce:true,webvisor:true});`;
}

/** URL пикселя для <noscript>. Пустая строка, если id не задан. */
export function metrikaPixelUrl(): string {
  if (!YM_ID) return "";
  return `https://mc.yandex.ru/watch/${YM_ID}`;
}

/** Отправить достижение цели (no-op без id или вне браузера). */
export function reachGoal(goalId: string, params?: Record<string, unknown>): void {
  if (typeof window === "undefined" || !window.ym || !YM_ID) return;
  try {
    window.ym(Number(YM_ID), "reachGoal", goalId, params);
  } catch {
    // Метрика не должна ломать пользовательский флоу
  }
}
