import { Metadata } from "next";
import { PageHeader } from "@/shared/ui/PageHeader";
import { siteConfig } from "@/shared/config/site";

export const metadata: Metadata = {
  title: "Политика конфиденциальности",
  description: "Политика обработки персональных данных пользователей сервиса защищённого сетевого доступа.",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-gray-100 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-800">
      <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{title}</h2>
      <div className="mt-3 space-y-2">{children}</div>
    </div>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-sm leading-relaxed text-gray-600 dark:text-gray-400">{children}</p>;
}

function Li({ children }: { children: React.ReactNode }) {
  return <li className="text-sm text-gray-600 dark:text-gray-400">{children}</li>;
}

export default function PrivacyPage() {
  return (
    <>
      <PageHeader
        title="Политика конфиденциальности"
        description="Политика обработки персональных данных"
      />
      <section className="mx-auto max-w-3xl px-4 py-12">
        <div className="space-y-6">
          <Section title="1. Общие положения">
            <P>
              Настоящая Политика конфиденциальности (далее — «Политика») определяет порядок обработки
              и защиты персональных данных пользователей сервиса защищённого сетевого доступа,
              управляемого {siteConfig.ipName} (ИНН: {siteConfig.inn}) (далее — «Оператор»).
            </P>
            <P>
              Политика разработана в соответствии с Федеральным законом от 27.07.2006 № 152-ФЗ
              «О персональных данных».
            </P>
          </Section>

          <Section title="2. Какие данные мы собираем">
            <P>Для оказания услуги Оператор обрабатывает следующие данные:</P>
            <ul className="list-disc space-y-1 pl-5">
              <Li>Идентификатор пользователя в Telegram (user ID)</Li>
              <Li>Адрес электронной почты (при обращении в поддержку)</Li>
              <Li>Данные платежа (через платёжную систему ЮKassa)</Li>
            </ul>
            <P>Оператор не собирает и не хранит содержимое трафика пользователей.</P>
          </Section>

          <Section title="3. Цели обработки данных">
            <P>Персональные данные обрабатываются в целях:</P>
            <ul className="list-disc space-y-1 pl-5">
              <Li>Оказания услуги защищённого сетевого доступа</Li>
              <Li>Идентификации пользователя при подключении</Li>
              <Li>Обработки платежей</Li>
              <Li>Оказания технической поддержки</Li>
              <Li>Исполнения требований законодательства</Li>
            </ul>
          </Section>

          <Section title="4. Защита данных">
            <P>
              Оператор принимает необходимые организационные и технические меры для защиты
              персональных данных от неправомерного доступа, уничтожения, изменения, блокирования,
              копирования и распространения.
            </P>
            <P>
              Платёжные данные обрабатываются платёжной системой ЮKassa и не хранятся на серверах Оператора.
            </P>
          </Section>

          <Section title="5. Передача данных третьим лицам">
            <P>Персональные данные передаются исключительно для целей, указанных в настоящей Политике:</P>
            <ul className="list-disc space-y-1 pl-5">
              <Li>Платёжной системе ЮKassa — для обработки платежей</Li>
              <Li>Мессенджеру Telegram — для доставки настроек и уведомлений</Li>
            </ul>
            <P>
              Оператор не продаёт, не сдаёт в аренду и не передаёт персональные данные третьим
              лицам для иных целей.
            </P>
          </Section>

          <Section title="6. Сроки хранения данных">
            <P>
              Персональные данные хранятся в течение срока действия подписки и в течение 1 (одного)
              года после окончания последней подписки, если иное не предусмотрено законодательством.
            </P>
          </Section>

          <Section title="7. Права пользователя">
            <P>
              Пользователь вправе запросить информацию об обработке своих персональных данных, а
              также потребовать их удаления, обратившись в поддержку: {siteConfig.supportEmail}.
            </P>
          </Section>

          <Section title="8. Контактная информация">
            <div className="space-y-1 text-sm text-gray-600 dark:text-gray-400">
              <p>{siteConfig.ipName}</p>
              <p>ИНН: {siteConfig.inn}</p>
              {siteConfig.ogrnip && <p>ОГРНИП: {siteConfig.ogrnip}</p>}
              {siteConfig.supportPhone && <p>Телефон: {siteConfig.supportPhone}</p>}
              <p>E-mail: {siteConfig.supportEmail}</p>
              <p>Telegram: {siteConfig.supportTelegramHandle}</p>
              {siteConfig.postalAddress && <p>Адрес для корреспонденции: {siteConfig.postalAddress}</p>}
            </div>
          </Section>
        </div>
      </section>
    </>
  );
}
