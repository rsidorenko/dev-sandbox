"use client";

import Link from "next/link";
import { siteConfig } from "@/shared/config/site";
import { reachGoal } from "@/shared/lib/analytics";
import { AuroraBackground } from "@/widgets/landing/AuroraBackground";

export function HeroSection() {
  return (
    <section className="relative overflow-hidden bg-gradient-to-br from-brand-900 via-brand-800 to-brand-950 py-24 text-white sm:py-32">
      <AuroraBackground />

      <div className="relative mx-auto max-w-4xl px-4 text-center">
        <span className="inline-block animate-fade-in-up rounded-full bg-white/10 px-4 py-1.5 text-xs font-medium tracking-wide [animation-delay:0ms]">
          Защищённое соединение
        </span>
        <h1 className="mt-6 animate-fade-in-up text-4xl font-extrabold leading-tight tracking-tight sm:text-5xl lg:text-6xl [animation-delay:100ms]">
          Защищённое соединение
          <br />
          <span className="bg-gradient-to-r from-brand-200 via-brand-400 to-brand-300 bg-clip-text text-transparent [background-size:200%_auto] animate-shimmer">
            без сложных настроек
          </span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl animate-fade-in-up text-lg leading-relaxed text-brand-100 [animation-delay:200ms]">
          Подключитесь за пару минут и используйте защищённое соединение на телефоне,
          компьютере и других устройствах. Персональный шифрованный туннель для защиты
          трафика в публичных сетях.
        </p>
        <div className="mt-10 flex animate-fade-in-up flex-col items-center gap-4 sm:flex-row sm:justify-center [animation-delay:300ms]">
          <Link
            href="/trial"
            onClick={() => reachGoal("trial_click")}
            className="rounded-xl bg-white px-8 py-4 text-sm font-bold text-brand-800 shadow-lg transition hover:-translate-y-0.5 hover:bg-brand-50 hover:shadow-xl"
          >
            🎁 Попробовать {siteConfig.trialDays} дня бесплатно
          </Link>
          <a
            href="#tariffs"
            onClick={() => reachGoal("choose_plan")}
            className="rounded-xl bg-brand-500 px-8 py-4 text-sm font-bold text-white shadow-lg transition hover:-translate-y-0.5 hover:bg-brand-400 hover:shadow-xl"
          >
            Выбрать тариф
          </a>
        </div>
        <p className="mt-6 animate-fade-in-up text-xs text-brand-200 [animation-delay:400ms]">
          🛡 Гарантия возврата {siteConfig.refundDays} дней · оплата картой и СБП · активация сразу после оплаты
        </p>
      </div>
    </section>
  );
}
