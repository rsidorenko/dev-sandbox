import { Metadata } from "next";
import { PageHeader } from "@/components/PageHeader";
import { siteConfig } from "@/config/site";

export const metadata: Metadata = {
  title: "Условия возврата",
  description: "Условия возврата средств за услугу защищённого сетевого доступа.",
};

const sections = [
  {
    title: "1. Общие условия",
    items: [
      "Заказчик вправе запросить полный возврат денежных средств в течение 7 (семи) календарных дней с момента оплаты подписки.",
      "Возврат средств осуществляется на тот же платёжный инструмент, с которого была произведена оплата (банковская карта, СБП, электронный кошелёк).",
    ],
  },
  {
    title: "2. Порядок оформления возврата",
    items: [
      "Для оформления возврата необходимо:",
    ],
    list: [
      `Написать в поддержку через Telegram: ${siteConfig.supportTelegramHandle}`,
      `Или отправить письмо на e-mail: ${siteConfig.supportEmail}`,
      "Указать идентификатор платежа (приходит в чеке после оплаты).",
    ],
    extra: ["Запрос рассматривается в течение 2 (двух) рабочих дней."],
  },
  {
    title: "3. Сроки возврата",
    items: [
      "Возврат денежных средств осуществляется в течение 5 (пяти) рабочих дней с момента подтверждения запроса.",
      "Фактическое зачисление средств на платёжный инструмент Заказчика может занять до 10 (десяти) рабочих дней в зависимости от платёжной системы.",
    ],
  },
  {
    title: "4. Частичный возврат",
    items: [
      "По истечении 7 календарных дней с момента оплаты полный возврат не предусмотрен.",
      "Частичный возврат (пропорционально неиспользованному периоду) возможен в исключительных случаях по решению Исполнителя.",
    ],
  },
  {
    title: "5. Отказ в возврате",
    items: ["Возврат может быть отказан в случаях:"],
    list: [
      "Нарушения Заказчиком условий публичной оферты;",
      "Использования услуги в целях, противоречащих законодательству РФ;",
      "Передачи доступа третьим лицам.",
    ],
  },
];

export default function RefundPage() {
  return (
    <>
      <PageHeader
        title="Условия возврата"
        description="Порядок и условия возврата денежных средств"
      />
      <section className="mx-auto max-w-3xl px-4 py-12">
        <div className="space-y-6">
          {sections.map((s) => (
            <div
              key={s.title}
              className="rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800"
            >
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{s.title}</h2>
              <div className="mt-3 space-y-2">
                {s.items.map((t, i) => (
                  <p key={i} className="text-sm leading-relaxed text-gray-600 dark:text-gray-400">{t}</p>
                ))}
                {s.list && (
                  <ul className="list-disc space-y-1 pl-5">
                    {s.list.map((l, i) => (
                      <li key={i} className="text-sm text-gray-600 dark:text-gray-400">{l}</li>
                    ))}
                  </ul>
                )}
                {s.extra?.map((t, i) => (
                  <p key={i} className="text-sm leading-relaxed text-gray-600 dark:text-gray-400">{t}</p>
                ))}
              </div>
            </div>
          ))}

          {/* Contacts */}
          <div className="rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">6. Контакты</h2>
            <div className="mt-3 space-y-1 text-sm text-gray-600 dark:text-gray-400">
              <p>{siteConfig.ipName}</p>
              <p>ИНН: {siteConfig.inn}</p>
              {siteConfig.ogrnip && <p>ОГРНИП: {siteConfig.ogrnip}</p>}
              {siteConfig.supportPhone && <p>Телефон: {siteConfig.supportPhone}</p>}
              <p>E-mail: {siteConfig.supportEmail}</p>
              <p>Telegram: {siteConfig.supportTelegramHandle}</p>
              {siteConfig.postalAddress && <p>Адрес для корреспонденции: {siteConfig.postalAddress}</p>}
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
