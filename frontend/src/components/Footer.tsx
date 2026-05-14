import Link from "next/link";
import { siteConfig } from "@/config/site";

export function Footer() {
  return (
    <footer className="border-t border-gray-100 bg-gray-50">
      <div className="mx-auto max-w-6xl px-4 py-10">
        <div className="grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="text-lg font-bold text-brand-700">Bravada</p>
            <p className="mt-2 text-sm text-gray-500">
              Защищённый сетевой доступ для безопасной работы в интернете.
            </p>
          </div>

          <div>
            <p className="text-sm font-semibold text-gray-900">Навигация</p>
            <nav className="mt-2 flex flex-col gap-1.5">
              <Link
                href="/#how-it-works"
                className="text-sm text-gray-500 transition hover:text-brand-600"
              >
                Как это работает
              </Link>
              <Link
                href="/#tariffs"
                className="text-sm text-gray-500 transition hover:text-brand-600"
              >
                Тарифы
              </Link>
              <Link
                href="/#faq"
                className="text-sm text-gray-500 transition hover:text-brand-600"
              >
                FAQ
              </Link>
            </nav>
          </div>

          <div>
            <p className="text-sm font-semibold text-gray-900">Документы</p>
            <nav className="mt-2 flex flex-col gap-1.5">
              <Link
                href="/offer"
                className="text-sm text-gray-500 transition hover:text-brand-600"
              >
                Публичная оферта
              </Link>
              <Link
                href="/privacy"
                className="text-sm text-gray-500 transition hover:text-brand-600"
              >
                Политика конфиденциальности
              </Link>
              <Link
                href="/refund"
                className="text-sm text-gray-500 transition hover:text-brand-600"
              >
                Условия возврата
              </Link>
            </nav>
          </div>

          <div>
            <p className="text-sm font-semibold text-gray-900">Контакты</p>
            <div className="mt-2 flex flex-col gap-1.5 text-sm text-gray-500">
              <a
                href={siteConfig.supportTelegram}
                target="_blank"
                rel="noopener noreferrer"
                className="transition hover:text-brand-600"
              >
                {siteConfig.supportTelegramHandle}
              </a>
              <a
                href={`mailto:${siteConfig.supportEmail}`}
                className="transition hover:text-brand-600"
              >
                {siteConfig.supportEmail}
              </a>
              <Link href="/contacts" className="transition hover:text-brand-600">
                Все контакты
              </Link>
            </div>
          </div>
        </div>

        <div className="mt-8 border-t border-gray-200 pt-6 text-center text-xs text-gray-400">
          <p>
            &copy; {new Date().getFullYear()} {siteConfig.ipName}. Все права
            защищены. ИНН: {siteConfig.inn}
          </p>
          <p className="mt-1">
            Услуга защищённого сетевого доступа — персональный шифрованный
            туннель для безопасности вашего трафика.
          </p>
        </div>
      </div>
    </footer>
  );
}
