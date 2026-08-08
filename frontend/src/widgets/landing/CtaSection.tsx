"use client";

import Link from "next/link";
import { siteConfig } from "@/shared/config/site";
import { reachGoal } from "@/shared/lib/analytics";
import { AuroraBackground } from "@/widgets/landing/AuroraBackground";
import { Reveal } from "@/shared/ui/Reveal";

export function CtaSection() {
  return (
    <section className="relative overflow-hidden bg-brand-900 py-20 text-white">
      <AuroraBackground />
      <Reveal className="relative mx-auto max-w-3xl px-4 text-center">
        <h2 className="text-3xl font-bold tracking-tight">
          Попробуйте Bravada бесплатно
        </h2>
        <p className="mt-4 text-brand-200">
          Подключение занимает несколько минут. {siteConfig.trialDays} дня бесплатно, без привязки карты.
        </p>
        <div className="mt-8 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
          <Link
            href="/trial"
            onClick={() => reachGoal("trial_click")}
            className="rounded-xl bg-white px-8 py-4 text-sm font-bold text-brand-800 shadow-lg transition hover:-translate-y-0.5 hover:bg-brand-50"
          >
            Попробовать бесплатно
          </Link>
          <a
            href="#tariffs"
            onClick={() => reachGoal("choose_plan")}
            className="rounded-xl border border-white/20 px-8 py-4 text-sm font-semibold text-white transition hover:bg-white/10"
          >
            Выбрать тариф
          </a>
        </div>
      </Reveal>
    </section>
  );
}
