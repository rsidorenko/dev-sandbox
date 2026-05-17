"use client";

import { Suspense, useState, useEffect, type FormEvent } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { paymentApi } from "@/entities/payment/api";
import { useAuth } from "@/shared/lib/auth";
import { siteConfig } from "@/shared/config/site";
import { Alert } from "@/shared/ui/Alert";

const EXTRA_DEVICE_PRICE = 80;
const DEFAULT_DEVICES = 5;

function calcTotal(basePrice: number, months: number, devices: number): number {
  const extra = Math.max(0, devices - DEFAULT_DEVICES);
  return basePrice + extra * EXTRA_DEVICE_PRICE * months;
}

export default function PaymentPage() {
  return (
    <Suspense
      fallback={
        <section className="flex min-h-[70vh] items-center justify-center">
          <p className="text-gray-500">Загрузка...</p>
        </section>
      }
    >
      <PaymentForm />
    </Suspense>
  );
}

function PaymentForm() {
  const { planId } = useParams<{ planId: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { loading: authLoading, authenticated } = useAuth();
  const [deviceCount, setDeviceCount] = useState(() => {
    const d = searchParams.get("devices");
    return d ? Math.max(DEFAULT_DEVICES, parseInt(d) || DEFAULT_DEVICES) : DEFAULT_DEVICES;
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
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Тариф не найден</h1>
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

  const months = tariff.durationDays / 30;
  const extraDevices = Math.max(0, deviceCount - DEFAULT_DEVICES);
  const extraCost = extraDevices * EXTRA_DEVICE_PRICE * months;
  const total = calcTotal(tariff.price, months, deviceCount);

  const handlePay = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg("");
    setLoading(true);

    const result = await paymentApi.create(tariff.id, deviceCount);
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
          href="/#tariffs"
          className="text-sm font-medium text-brand-600 transition hover:text-brand-700 dark:text-brand-400"
        >
          &larr; Назад к тарифам
        </Link>

        <div className="mt-6 rounded-2xl border border-gray-100 bg-white p-8 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Оформление подписки</h1>

          <div className="mt-6 space-y-3">
            <div className="flex justify-between text-sm">
              <span className="text-gray-500 dark:text-gray-400">Тариф</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{tariff.label}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500 dark:text-gray-400">Период</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{tariff.durationDays} дней</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-gray-500 dark:text-gray-400">Подписка</span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{tariff.price} ₽</span>
            </div>
          </div>

          <div className="mt-6">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Количество устройств: <strong>{deviceCount}</strong>
            </label>
            <input
              type="range"
              min={5}
              max={20}
              value={deviceCount}
              onChange={(e) => setDeviceCount(parseInt(e.target.value))}
              className="mt-2 w-full accent-brand-600"
            />
            <div className="flex justify-between text-xs text-gray-400">
              <span>5</span><span>10</span><span>15</span><span>20</span>
            </div>
            {extraDevices > 0 && (
              <p className="mt-1 text-xs text-gray-400">
                Доп. устройства: {extraDevices} × {EXTRA_DEVICE_PRICE} ₽ × {months} мес. = {extraCost} ₽
              </p>
            )}
          </div>

          <div className="mt-6 flex items-center justify-between rounded-xl bg-gray-50 px-4 py-3 dark:bg-zinc-700/50">
            <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Итого</span>
            <span className="text-xl font-bold text-gray-900 dark:text-gray-100">{total} ₽</span>
          </div>

          {errorMsg && <Alert className="mt-4">{errorMsg}</Alert>}

          {paymentResult && paymentResult.status === "payment_unavailable" && (
            <Alert variant="info" className="mt-4">
              <p className="text-sm text-brand-900 dark:text-brand-200">{paymentResult.message}</p>
              <p className="mt-2 text-sm font-medium text-brand-700 dark:text-brand-300">
                Сумма: {paymentResult.amount_rubles} ₽
              </p>
            </Alert>
          )}

          <button
            onClick={handlePay}
            disabled={loading}
            className="mt-6 w-full rounded-xl bg-brand-600 py-3 text-sm font-bold text-white transition hover:bg-brand-700 disabled:opacity-50"
          >
            {loading ? "Обработка..." : `Оплатить ${total} ₽`}
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
