// Легенда статусов подписки над таблицей пользователей (C+D).
// Статусы взяты из реальных значений subscription_snapshots.state_label
// (active / expired / cancelled) + случай «нет снапшота» (—), когда пользователь
// зарегистрирован, но никогда не активировал подписку/триал. Не выдумываем значения.

export type StatusTone = "active" | "warn" | "muted";

export interface StatusDescriptor {
  key: string;
  badge: string;
  title: string;
  tone: StatusTone;
}

export const STATUS_DESCRIPTORS: StatusDescriptor[] = [
  {
    key: "active",
    badge: "active",
    title: "Подписка действует, ключи работают.",
    tone: "active",
  },
  {
    key: "expired",
    badge: "expired",
    title: "Подписка истекла, ключи деактивированы. 20 дней льготы, затем удаление.",
    tone: "warn",
  },
  {
    key: "cancelled",
    badge: "cancelled",
    title: "Отменена пользователем.",
    tone: "muted",
  },
  {
    key: "none",
    badge: "—",
    title:
      "Нет подписки: зарегистрирован, но никогда не активировал триал/оплату. Продлить нельзя — нет тарифа.",
    tone: "muted",
  },
];

function toneClass(tone: StatusTone): string {
  switch (tone) {
    case "active":
      return "bg-green-100 text-green-700 dark:bg-green-950/30 dark:text-green-400";
    case "warn":
      return "bg-amber-100 text-amber-700 dark:bg-amber-950/30 dark:text-amber-400";
    default:
      return "bg-gray-100 text-gray-500 dark:bg-zinc-700 dark:text-gray-300";
  }
}

export function StatusLegend() {
  return (
    <div className="mb-5 flex flex-col gap-2.5 rounded-xl border border-gray-200 bg-gray-50/60 p-4 dark:border-zinc-700 dark:bg-zinc-800/40 sm:flex-row sm:flex-wrap sm:gap-x-5 sm:gap-y-2">
      {STATUS_DESCRIPTORS.map((s) => (
        <div key={s.key} className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
          <span className={`inline-flex min-w-[3.5rem] items-center justify-center rounded-md px-2 py-0.5 text-xs font-medium ${toneClass(s.tone)}`}>
            {s.badge}
          </span>
          <span>{s.title}</span>
        </div>
      ))}
    </div>
  );
}
