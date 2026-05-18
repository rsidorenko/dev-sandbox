import { TelegramButton } from "@/shared/ui/TelegramButton";

const features = [
  {
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
      </svg>
    ),
    title: "Шифрование трафика",
    description: "Все данные между вашим устройством и сервером передаются по зашифрованному каналу — перехват невозможен.",
  },
  {
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5a17.92 17.92 0 01-8.716-2.247m0 0A8.966 8.966 0 003 12c0-1.472.253-2.884.716-4.197" />
      </svg>
    ),
    title: "Защита в публичных Wi-Fi",
    description: "Безопасно используйте интернет в кафе, аэропортах и отелях — ваш трафик защищён от прослушивания.",
  },
  {
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 1.5H8.25A2.25 2.25 0 006 3.75v16.5a2.25 2.25 0 002.25 2.25h7.5A2.25 2.25 0 0018 20.25V3.75a2.25 2.25 0 00-2.25-2.25H13.5m-3 0V3h3V1.5m-3 0h3m-3 18.75h3" />
      </svg>
    ),
    title: "Все устройства",
    description: "Подключайте от 5 до 20 устройств — iOS, Android, Windows, macOS и Linux.",
  },
  {
    icon: (
      <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
      </svg>
    ),
    title: "Мгновенное подключение",
    description: "Активация за пару минут после оплаты. Настройки и инструкция — прямо в Telegram-боте.",
  },
];

export function FeaturesSection() {
  return (
    <section id="features" className="py-20 bg-gray-50 dark:bg-zinc-800/50">
      <div className="mx-auto max-w-6xl px-4">
        <div className="text-center">
          <h2 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
            Что вы получаете
          </h2>
          <p className="mt-3 text-gray-500 dark:text-gray-400">
            Надёжная защита трафика без сложных настроек
          </p>
        </div>

        <div className="mt-14 grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          {features.map((f, i) => (
            <div
              key={i}
              className="rounded-2xl border border-gray-100 bg-white p-6 transition hover:shadow-md dark:border-zinc-700 dark:bg-zinc-800 dark:hover:border-zinc-600"
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-brand-100 text-brand-600 dark:bg-brand-900/40 dark:text-brand-400">
                {f.icon}
              </div>
              <h3 className="mt-4 text-base font-semibold text-gray-900 dark:text-gray-100">
                {f.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-gray-500 dark:text-gray-400">
                {f.description}
              </p>
            </div>
          ))}
        </div>

        <div className="mt-12 text-center">
          <TelegramButton
            className="inline-flex items-center gap-2 rounded-xl bg-brand-600 px-8 py-3.5 text-sm font-bold text-white shadow-lg transition hover:bg-brand-700"
            label="Перейти к боту"
          />
        </div>
      </div>
    </section>
  );
}
