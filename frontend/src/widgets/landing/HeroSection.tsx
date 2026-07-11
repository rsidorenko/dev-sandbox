"use client";

import { siteConfig } from "@/shared/config/site";
import { reachGoal } from "@/shared/lib/analytics";

const TRIAL_URL = `https://t.me/${siteConfig.botUsername}?start=trial`;

export function HeroSection() {
  return (
    <section className="relative overflow-hidden bg-gradient-to-br from-brand-900 via-brand-800 to-brand-950 py-24 text-white sm:py-32">
      <div className="absolute inset-0 opacity-10">
        <div className="absolute -left-20 -top-20 h-96 w-96 rounded-full bg-brand-400 blur-3xl" />
        <div className="absolute -bottom-20 -right-20 h-96 w-96 rounded-full bg-brand-300 blur-3xl" />
      </div>

      <div className="relative mx-auto max-w-4xl px-4 text-center">
        <span className="inline-block rounded-full bg-white/10 px-4 py-1.5 text-xs font-medium tracking-wide">
          Доступ к сервисам без ограничений
        </span>
        <h1 className="mt-6 text-4xl font-extrabold leading-tight tracking-tight sm:text-5xl lg:text-6xl">
          Свободный интернет
          <br />
          <span className="text-brand-300">на любом устройстве</span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-brand-100">
          Смотрите YouTube и Instagram, пользуйтесь банками и мессенджерами за
          границей, обходите региональные ограничения и защитите данные в публичном
          Wi-Fi. Персональный шифрованный туннель — активация за пару минут.
        </p>
        <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
          <a
            href="#tariffs"
            onClick={() => reachGoal("choose_plan")}
            className="rounded-xl bg-white px-8 py-3.5 text-sm font-bold text-brand-800 shadow-lg transition hover:bg-brand-50"
          >
            Выбрать тариф
          </a>
          <a
            href={TRIAL_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => reachGoal("trial_click")}
            className="rounded-xl bg-brand-500 px-8 py-3.5 text-sm font-bold text-white shadow-lg transition hover:bg-brand-400"
          >
            🎁 {siteConfig.trialDays} дня бесплатно
          </a>
          <a
            href="#how-it-works"
            className="rounded-xl border border-white/20 px-8 py-3.5 text-sm font-semibold text-white transition hover:bg-white/10"
          >
            Как это работает
          </a>
        </div>
        <p className="mt-6 text-xs text-brand-200">
          Оплата картой и СБП · активация сразу после оплаты
        </p>
      </div>
    </section>
  );
}
