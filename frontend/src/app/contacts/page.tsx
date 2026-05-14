import { Metadata } from "next";
import { PageHeader } from "@/components/PageHeader";
import { siteConfig } from "@/config/site";

export const metadata: Metadata = {
  title: "Контакты",
  description: "Контактная информация и реквизиты исполнителя услуги защищённого сетевого доступа.",
};

export default function ContactsPage() {
  return (
    <>
      <PageHeader
        title="Контакты и реквизиты"
        description="Как с нами связаться"
      />
      <section className="mx-auto max-w-3xl px-4 py-12">
        <div className="grid gap-8 sm:grid-cols-2">
          {/* Contacts */}
          <div className="rounded-2xl border border-gray-100 bg-white p-8">
            <h2 className="text-lg font-semibold text-gray-900">Связаться с нами</h2>
            <div className="mt-6 flex flex-col gap-4">
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  Telegram
                </p>
                <a
                  href={siteConfig.supportTelegram}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 text-sm font-medium text-brand-600 transition hover:text-brand-700"
                >
                  {siteConfig.supportTelegramHandle}
                </a>
              </div>
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  Электронная почта
                </p>
                <a
                  href={`mailto:${siteConfig.supportEmail}`}
                  className="mt-1 text-sm font-medium text-brand-600 transition hover:text-brand-700"
                >
                  {siteConfig.supportEmail}
                </a>
              </div>
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  Время ответа
                </p>
                <p className="mt-1 text-sm text-gray-600">
                  В среднем — в течение 2 часов
                </p>
              </div>
            </div>
          </div>

          {/* Requisites */}
          <div className="rounded-2xl border border-gray-100 bg-white p-8">
            <h2 className="text-lg font-semibold text-gray-900">Реквизиты</h2>
            <div className="mt-6 flex flex-col gap-3 text-sm text-gray-600">
              <div>
                <span className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  Исполнитель
                </span>
                <p className="mt-1">{siteConfig.ipName}</p>
              </div>
              <div>
                <span className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  ИНН
                </span>
                <p className="mt-1">{siteConfig.inn}</p>
              </div>
              <div>
                <span className="text-xs font-medium uppercase tracking-wide text-gray-400">
                  Платёжный оператор
                </span>
                <p className="mt-1">
                  Платёжный сервис ЮKassa
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Support note */}
        <div className="mt-8 rounded-2xl bg-brand-50 p-6">
          <h3 className="text-sm font-semibold text-brand-900">
            Нужна помощь с подключением?
          </h3>
          <p className="mt-2 text-sm text-brand-700">
            Если у вас возникли вопросы по настройке или использованию сервиса,
            напишите нам в Telegram — мы оперативно поможем.
          </p>
          <a
            href={siteConfig.supportTelegram}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-4 inline-block rounded-xl bg-brand-600 px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
          >
            Написать в поддержку
          </a>
        </div>
      </section>
    </>
  );
}
