"use client";

import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { siteConfig } from "@/config/site";

type Step = "email" | "code" | "error";

export default function LoginPage() {
  return (
    <Suspense fallback={<section className="flex min-h-[70vh] items-center justify-center"><p className="text-gray-500">Загрузка...</p></section>}>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [codeTtl, setCodeTtl] = useState(10);
  const [resendTimer, setResendTimer] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const startResendTimer = () => {
    setResendTimer(60);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      setResendTimer((t) => {
        if (t <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          timerRef.current = null;
          return 0;
        }
        return t - 1;
      });
    }, 1000);
  };

  const validateEmail = (e: string) => {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e);
  };

  const handleSendCode = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg("");

    if (!validateEmail(email)) {
      setErrorMsg("Введите корректный email");
      return;
    }

    setLoading(true);
    const result = await api.auth.sendCode(email);
    setLoading(false);

    if (!result.ok) {
      if (result.error === "rate_limited") {
        setErrorMsg("Слишком много попыток. Попробуйте позже.");
      } else {
        setErrorMsg("Не удалось отправить код. Попробуйте позже.");
      }
      return;
    }

    setCodeTtl(result.data.ttl_minutes || 10);
    setStep("code");
    startResendTimer();
  };

  const handleVerifyCode = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMsg("");

    if (!code || code.length < 4) {
      setErrorMsg("Введите код из письма");
      return;
    }

    setLoading(true);
    const result = await api.auth.verifyCode(email, code);
    setLoading(false);

    if (!result.ok) {
      if (result.error === "invalid_code") {
        setErrorMsg("Неверный код. Попробуйте снова.");
      } else if (result.error === "code_expired") {
        setErrorMsg("Код истёк. Запросите новый.");
        setStep("email");
      } else if (result.error === "email_not_linked") {
        setErrorMsg(
          "Этот email не привязан к аккаунту. Сначала привяжите его через Telegram-бота."
        );
      } else if (result.error === "too_many_attempts") {
        setErrorMsg("Слишком много попыток. Запросите новый код.");
        setStep("email");
      } else {
        setErrorMsg("Не удалось войти. Попробуйте позже.");
      }
      return;
    }

    if (result.data.token) {
      // Session is set by backend httponly cookie — no client-side storage needed
    }

    const next = searchParams.get("next") || "/dashboard";
    router.push(next);
  };

  const handleResend = async () => {
    if (resendTimer > 0) return;
    setErrorMsg("");
    setLoading(true);
    const result = await api.auth.sendCode(email);
    setLoading(false);
    if (result.ok) {
      startResendTimer();
    } else {
      setErrorMsg("Не удалось отправить код повторно.");
    }
  };

  return (
    <section className="flex min-h-[70vh] items-center justify-center py-20">
      <div className="mx-auto w-full max-w-md px-4">
        <div className="rounded-2xl border border-gray-100 bg-white p-8 shadow-sm dark:border-zinc-700 dark:bg-zinc-800">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Вход в аккаунт</h1>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
            Введите email, привязанный к вашему аккаунту
          </p>

          {errorMsg && (
            <div className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
              {errorMsg}
            </div>
          )}

          {step === "email" && (
            <form onSubmit={handleSendCode} className="mt-6 space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="email@example.com"
                  className="mt-1 block w-full rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20 dark:border-zinc-600 dark:bg-zinc-700 dark:text-gray-100"
                  disabled={loading}
                  autoFocus
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
              >
                {loading ? "Отправка..." : "Получить код"}
              </button>
            </form>
          )}

          {step === "code" && (
            <form onSubmit={handleVerifyCode} className="mt-6 space-y-4">
              <div>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  Код подтверждения отправлен на <strong>{email}</strong>
                </p>
              </div>
              <div>
                <label htmlFor="code" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Код подтверждения
                </label>
                <input
                  id="code"
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  placeholder="123456"
                  className="mt-1 block w-full rounded-lg border border-gray-200 px-4 py-2.5 text-center text-lg tracking-widest transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/20"
                  disabled={loading}
                  autoFocus
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
              >
                {loading ? "Проверка..." : "Войти"}
              </button>
              <div className="flex items-center justify-between text-sm">
                <button
                  type="button"
                  onClick={handleResend}
                  disabled={resendTimer > 0 || loading}
                  className="text-brand-600 transition hover:text-brand-700 disabled:text-gray-400"
                >
                  {resendTimer > 0 ? `Отправить повторно (${resendTimer}с)` : "Отправить код повторно"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setStep("email");
                    setCode("");
                    setErrorMsg("");
                  }}
                  className="text-gray-500 transition hover:text-gray-700"
                >
                  Изменить email
                </button>
              </div>
              <p className="text-xs text-gray-400">
                Код действителен {codeTtl} минут
              </p>
            </form>
          )}

          <div className="mt-6 border-t border-gray-100 pt-4 text-center text-sm text-gray-500 dark:border-zinc-700">
            <p>
              Нет аккаунта? Привяжите email через{" "}
              <a
                href={`https://t.me/${siteConfig.botUsername}`}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-brand-600 hover:text-brand-700"
              >
                Telegram-бота
              </a>
            </p>
          </div>

          <div className="mt-4 text-center">
            <Link
              href="/"
              className="text-sm font-medium text-brand-600 transition hover:text-brand-700"
            >
              &larr; На главную
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
