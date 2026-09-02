// Общие классы админ-таблиц и контролов в дизайн-системе основного сайта
// (Inter, brand-синий, rounded-2xl, gray/zinc-поверхности). Применяются на
// /admin/users и /admin/promocodes, чтобы обе страницы выглядели единой системой
// с ровными колонками и одинаковыми отступами.

// Каркас таблицы.
export const adminTableWrap =
  "touch-pan-x overflow-x-auto rounded-2xl border border-gray-200 bg-white shadow-sm [-webkit-overflow-scrolling:touch] dark:border-zinc-700 dark:bg-zinc-800";

export const adminTable = "min-w-max w-full border-collapse text-left text-sm";

// Шапка: приглушённый фон, капитель, трекинг — как у «меток» сайта.
export const adminTh =
  "bg-gray-50/70 px-4 py-3 text-xs font-semibold uppercase tracking-wider text-gray-500 dark:bg-zinc-800/70 dark:text-gray-400 whitespace-nowrap";

// Ячейка: единые отступы, вертикальное центрирование.
export const adminTd = "px-4 py-3 align-middle text-gray-700 dark:text-gray-300";

// Строка: разделитель снизу + ховер; last:border-0 убирает лишнюю черту.
export const adminRow =
  "border-b border-gray-100 transition-colors last:border-0 hover:bg-gray-50 dark:border-zinc-700/60 dark:hover:bg-zinc-800/50";

// Числа/даты: моноширинные цифры без переносов → ровный столбец по широте.
export const adminNum = "tabular-nums whitespace-nowrap";

// Моноширинный идентификатор (telegram id, код промокода).
export const adminMono = "font-mono whitespace-nowrap";

// Поле ввода тулбара (поиск).
export const adminInput =
  "rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 placeholder:text-gray-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100 dark:placeholder:text-gray-500";

// Кнопки тулбара/пагинации.
export const adminBtnPrimary =
  "inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50";

export const adminBtnSecondary =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3.5 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-40 dark:border-zinc-600 dark:bg-zinc-800 dark:text-gray-300 dark:hover:bg-zinc-700";

// Ссылка-таб навигации админки.
export const adminNavActive = "shrink-0 whitespace-nowrap rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white";
export const adminNavIdle =
  "shrink-0 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium text-gray-600 transition hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-zinc-800";
