import { siteConfig } from "@/shared/config/site";

export function DeliverySection() {
  return (
    <section id="delivery" className="py-20">
      <div className="mx-auto max-w-6xl px-4">
        <div className="text-center">
          <h2 className="text-3xl font-bold tracking-tight text-gray-900 dark:text-gray-100">
            Как вы получите услугу после оплаты
          </h2>
          <p className="mt-3 text-gray-500 dark:text-gray-400">
            Прозрачный процесс от оплаты до подключения
          </p>
        </div>

        <div className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {steps.map((s, i) => (
            <div
              key={i}
              className="rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-100 text-sm font-bold text-brand-700 dark:bg-brand-900/40 dark:text-brand-300">
                {i + 1}
              </div>
              <h3 className="mt-4 text-sm font-semibold text-gray-900 dark:text-gray-100">
                {s.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-gray-500 dark:text-gray-400">
                {s.text}
              </p>
            </div>
          ))}
        </div>

        <div className="mt-10 rounded-2xl bg-gray-50 p-6 text-center text-sm text-gray-500 dark:bg-zinc-800 dark:text-gray-400">
          После оплаты доступ активируется автоматически. Настройки подключения
          доступны в личном кабинете и Telegram-боте. Если у вас возникли вопросы —
          напишите в{" "}
          <a
            href={siteConfig.supportTelegram}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"
          >
            поддержку
          </a>
          .
        </div>
      </div>
    </section>
  );
}

const steps = [
  {
    title: "Выбор тарифа",
    text: "Выберите подходящий тариф (1, 3 или 6 месяцев) и укажите количество устройств.",
  },
  {
    title: "Оформление подписки",
    text: "Нажмите «Оформить подписку» и пройдите оплату. Банковская карта, СБП и другие способы.",
  },
  {
    title: "Получение доступа",
    text: "После подтверждения оплаты доступ активируется автоматически. Настройки подключения доступны в личном кабинете и Telegram-боте.",
  },
  {
    title: "Подключение",
    text: "Используйте полученные настройки в совместимых приложениях для защищённого соединения. Инструкция — в личном кабинете и Telegram-боте.",
  },
];
