export function DeliverySection() {
  return (
    <section id="delivery" className="py-20">
      <div className="mx-auto max-w-6xl px-4">
        <div className="text-center">
          <h2 className="text-3xl font-bold tracking-tight text-gray-900">
            Как вы получите услугу после оплаты
          </h2>
          <p className="mt-3 text-gray-500">
            Прозрачный процесс от оплаты до подключения
          </p>
        </div>

        <div className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {steps.map((s, i) => (
            <div
              key={i}
              className="rounded-2xl border border-gray-100 bg-white p-6"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-100 text-sm font-bold text-brand-700">
                {i + 1}
              </div>
              <h3 className="mt-4 text-sm font-semibold text-gray-900">
                {s.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-gray-500">
                {s.text}
              </p>
            </div>
          ))}
        </div>

        <div className="mt-10 rounded-2xl bg-gray-50 p-6 text-center text-sm text-gray-500">
          Все настройки и инструкции доставляются автоматически через
          Telegram-бота. Если у вас возникли вопросы — напишите в{" "}
          <a
            href="#delivery"
            className="font-medium text-brand-600 hover:text-brand-700"
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
    title: "Переход в Telegram-бота",
    text: "Нажимаете «Оплатить» на сайте и переходите в Telegram-бота.",
  },
  {
    title: "Выбор тарифа и устройств",
    text: "В боте выбираете подходящий тариф (1, 3 или 6 месяцев) и количество устройств (до 20).",
  },
  {
    title: "Оплата подписки",
    text: "Бот формирует ссылку на страницу оплаты. Банковская карта, СБП и другие способы.",
  },
  {
    title: "Получение настроек",
    text: "После оплаты бот автоматически отправляет персональные настройки защищённого подключения.",
  },
];
