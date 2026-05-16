"use client";

import { useState, useEffect, type FormEvent } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { siteConfig } from "@/config/site";

export default function PaymentPage() {
  const { planId } = useParams<{ planId: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { loading: authLoading, authenticated } = useAuth();
  const [deviceCount, setDeviceCount] = useState(() => {
    const d = searchParams.get("devices");
    return d ? parseInt(d) || 5 : 5;
  });
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [paymentResult, setPaymentResult] = useState<{
    status: string;
    amount_rubles: number;
    message: string;
  } | null>(null);

  const tariff = siteConfig.tariffs.find((t) => t.id === planId);

  useEffect(() => {
    if (!authLoading && !authenticated) {
      router.push(`/login?next=/payment/${planId}`);
    }
  }, [authLoading, authenticated, router, planId]);

  if (!tariff) {
    return (
      <section className="flex min-h-[60vh] items-center justify-center py-20">
        <div className="mx-auto max-w-md px-4 text-center">
          <h1 className="text-2xl font-bold text-gray-900">Тариф не найден</h1>
          <Link
            href="/#tariffs"
            className="mt-6 inline-block rounded-xl bg-brand-600 px-6 py-3 text-sm font-semibold text-white transition hover:bg-brand-700"
          >
            Выбрать тариф
          </Link>
        </div>
      </section>
    );
  }

  const handlePay = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg("");
    setLoading(true);

    const result = await api.payment.create(tariff.id, deviceCount);
    setLoading(false);

    if (!result.ok) {
      if (result.error === "unauthorized") {
        router.push(`/login?next=/payment/${planId}`);
        return;
      }
      setErrorMsg("Не удалось создать платёж. Попробуйте позже.");
      return;
    }

    const data = result.data;
    if (data.payment_url) {
      window.location.href = data.payment_url;
      return;
    }

    setPaymentResult({
      status: data.status,
      amount_rubles: data.amount_rubles,
      message: data.message || "",
    });
  };

  return (
    <section className="py-12">
      <div className="mx-auto max-w-lg px-4">
        <Link
          href="/dashboard"
          className="text-sm font-medium text-brand-600 transition hover:text-brand-700 dark:text-brand-400"
        >
          &larr; Назад
        </Link>

        <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-8 shadow-sm">
          <h1 className="text-xl font-bold text-gray-900">Оформление подписки</h1>

          <div className="mt-6 space-y-3">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Тариф</span>
              <span className="font-medium text-gray-900">{tariff.label}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500">Период</span>
              <span className="font-medium text-gray-900">{tariff.durationDays} дней</span>
            </div>
          </div>

          {/* Device count selector */}
          <div className="mt-6">
            <label className="block text-sm font-medium text-gray-700">
              Количество устройств: {deviceCount}
            </label>
            <input
              type="range"
              min={1}
              max={20}
              value={deviceCount}
              onChange={(e) => setDeviceCount(parseInt(e.target.value))}
              className="mt-2 w-full accent-brand-600"
            />
            <div className="flex justify-between text-xs text-gray-400">
              <span>1</span>
              <span>20</span>
            </div>
          </div>

          {errorMsg && (
            <div className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {errorMsg}
            </div>
          )}

          {paymentResult && paymentResult.status === "payment_unavailable" && (
            <div className="mt-4 rounded-lg bg-brand-50 px-4 py-3">
              <p className="text-sm text-brand-900">{paymentResult.message}</p>
              <p className="mt-2 text-sm font-medium text-brand-700">
                Сумма: {paymentResult.amount_rubles} ₽
              </p>
            </div>
          )}

          <button
            onClick={handlePay}
            disabled={loading}
            className="mt-6 w-full rounded-xl bg-brand-600 py-3 text-sm font-bold text-white transition hover:bg-brand-700 disabled:opacity-50"
          >
            {loading ? "Обработка..." : `Оформить подписку`}
          </button>

          <p className="mt-4 text-center text-xs text-gray-400">
            Нажимая кнопку, вы соглашаетесь с{" "}
            <Link href="/offer" className="text-brand-600 underline">
              условиями оферты
            </Link>
          </p>
        </div>
      </div>
    </section>
  );
}
