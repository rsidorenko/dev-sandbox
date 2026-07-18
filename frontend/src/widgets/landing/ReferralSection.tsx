"use client";

import Link from "next/link";
import { reachGoal } from "@/shared/lib/analytics";
import { Reveal } from "@/shared/ui/Reveal";

/**
 * Секция реферальной программы на лендинге.
 * Программа реализована в боте/ЛК (features/subscription/SubscriptionCard.tsx);
 * входящие ?ref= обрабатываются в shared/lib/referralCapture.tsx.
 * Условия: L1 — до 35% с оплат приглашённого, L2 — до 5% с оплат его друзей.
 */
export function ReferralSection() {
  return (
    <section id="referral" className="py-20">
      <div className="mx-auto max-w-5xl px-4">
        <Reveal>
        <div className="overflow-hidden rounded-3xl bg-gradient-to-br from-brand-700 to-brand-900 px-6 py-12 text-center text-white shadow-lg sm:px-12">
          <h2 className="text-3xl font-bold tracking-tight">
            Приведи друга — получай до 35%
          </h2>
          <p className="mx-auto mt-3 max-w-2xl text-brand-100">
            Поделитесь личной ссылкой. Вы зарабатываете с каждой оплаты друга —
            и продолжаете зарабатывать, когда он приглашает своих.
          </p>

          <div className="mx-auto mt-8 grid max-w-3xl gap-4 sm:grid-cols-2">
            <div className="rounded-2xl bg-white/10 p-6 text-left">
              <p className="text-3xl font-extrabold text-brand-300">до 35%</p>
              <p className="mt-1 text-sm text-brand-100">
                с каждой оплаты приглашённого вами друга (1-й уровень)
              </p>
            </div>
            <div className="rounded-2xl bg-white/10 p-6 text-left">
              <p className="text-3xl font-extrabold text-brand-300">до 5%</p>
              <p className="mt-1 text-sm text-brand-100">
                с оплат тех, кого пригласил ваш друг (2-й уровень)
              </p>
            </div>
          </div>

          <div className="mt-8">
            <Link
              href="/dashboard"
              onClick={() => reachGoal("referral_open")}
              className="inline-block rounded-xl bg-white px-8 py-3.5 text-sm font-bold text-brand-800 shadow-lg transition hover:bg-brand-50"
            >
              Открыть реферальную программу
            </Link>
            <p className="mt-3 text-xs text-brand-200">
              Накопленный баланс можно потратить на оплату подписки
            </p>
          </div>
        </div>
        </Reveal>
      </div>
    </section>
  );
}
