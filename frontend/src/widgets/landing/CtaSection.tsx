import { siteConfig } from "@/shared/config/site";

export function CtaSection() {
  return (
    <section className="bg-brand-900 py-20 text-white">
      <div className="mx-auto max-w-3xl px-4 text-center">
        <h2 className="text-3xl font-bold tracking-tight">
          Начните использовать защищённое соединение прямо сейчас
        </h2>
        <p className="mt-4 text-brand-200">
          Подключитесь за несколько минут и защитите свой трафик в любых сетях.
        </p>
        <div className="mt-8 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
          <a
            href="#tariffs"
            className="rounded-xl bg-white px-8 py-3.5 text-sm font-bold text-brand-800 shadow-lg transition hover:bg-brand-50"
          >
            Выбрать тариф
          </a>
          <a
            href={siteConfig.supportTelegram}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-xl border border-white/20 px-8 py-3.5 text-sm font-semibold text-white transition hover:bg-white/10"
          >
            Написать в поддержку
          </a>
        </div>
      </div>
    </section>
  );
}
