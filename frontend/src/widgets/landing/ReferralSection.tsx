"use client";

import Link from "next/link";
import { reachGoal } from "@/shared/lib/analytics";
import { Reveal } from "@/shared/ui/Reveal";

/**
 * Секция реферальной программы на лендинге.
 * Двусторонний оффер: приглашённому +3 дня, пригласившему кешбэк L1/L2.
 * Программа реализована в боте/ЛК (features/referral/ReferralCard.tsx).
 */
export function ReferralSection() {
  return (
    <section id="referral" className="py-20">
      <div className="mx-auto max-w-5xl px-4">
        <Reveal>
        <div className="overflow-hidden rounded-3xl bg-gradient-to-br from-brand-700 to-brand-900 px-6 py-12 text-center text-white shadow-lg sm:px-12">
          <h2 className="text-3xl font-bold tracking-tight">
            Пригласи друга — бонус получат оба
          </h2>
          <p className="mx-auto mt-3 max-w-2xl text-brand-100">
            Друг получает <b className="text-white">+3 дня</b> к первой подписке от месяца,
            а вы — кешбэк с его оплат.
          </p>

          <div className="mx-auto mt-8 grid max-w-3xl gap-4 sm:grid-cols-3">
            <div className="rounded-2xl bg-white/15 p-6 text-left ring-2 ring-white/40">
              <p className="text-3xl font-extrabold text-white">🎁 +3 дня</p>
              <p className="mt-1 text-sm text-brand-100">
                другу к первой подписке от месяца
              </p>
            </div>
            <div className="rounded-2xl bg-white/10 p-6 text-left">
              <p className="text-3xl font-extrabold text-brand-300">до 20%</p>
              <p className="mt-1 text-sm text-brand-100">
                кешбэк с оплат приглашённого (1-й уровень)
              </p>
            </div>
            <div className="rounded-2xl bg-white/10 p-6 text-left">
              <p className="text-3xl font-extrabold text-brand-300">до 2%</p>
              <p className="mt-1 text-sm text-brand-100">
                кешбэк с оплат друзей друга (2-й уровень)
              </p>
            </div>
          </div>

          <div className="mt-8">
            <Link
              href="/dashboard"
              onClick={() => reachGoal("referral_open")}
              className="inline-block rounded-xl bg-white px-8 py-3.5 text-sm font-bold text-brand-800 shadow-lg transition hover:bg-brand-50"
            >
              Получить реферальную ссылку
            </Link>
            <p className="mt-3 text-xs text-brand-200">
              Бонусные дни и кешбэк зачисляются автоматически
            </p>
          </div>
        </div>
        </Reveal>
      </div>
    </section>
  );
}
