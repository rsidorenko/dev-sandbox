"use client";
import { Suspense, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { authApi } from "./api";
import { userApi } from "@/entities/user/api";
import { clearToken, parseLoginToken, setToken } from "@/shared/lib/tokenStore";
import { safeNextPath } from "@/shared/lib/safeRedirect";
import { clearPendingReferralCode, readPendingReferralCode } from "@/shared/lib/referralCapture";
import { Alert } from "@/shared/ui/Alert";

export function LinkAccess({ next = "/dashboard" }: { next?: string }) {
  const router = useRouter();
  const [link, setLink] = useState("");
  const [generated, setGenerated] = useState<{subscription_url: string; expires_at: string} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const create = async () => {
    setBusy(true); setError("");
    try {
      const result = await authApi.requestLink(readPendingReferralCode() || undefined);
      if (!result.ok) { setError(result.error === "rate_limited" ? "Слишком много запросов. Попробуйте позже." : "Не удалось создать ссылку. Попробуйте позже."); return; }
      setGenerated(result.data); setCopied(false); clearPendingReferralCode();
    } finally { setBusy(false); }
  };
  const login = async (event: FormEvent) => {
    event.preventDefault(); setError("");
    const token = parseLoginToken(link);
    if (!token) { setError("Вставьте полную ссылку подписки."); return; }
    setBusy(true); setToken(token);
    try {
      const result = await userApi.profile();
      if (!result.ok) { clearToken(); setError("Ссылка недействительна, отозвана или срок неоплаченной ссылки истёк. Если вы уже оплатили, обратитесь в поддержку с номером платежа."); return; }
      router.push(next);
    } finally { setBusy(false); }
  };
  const inputClass = "mt-2 w-full rounded-lg border border-gray-300 bg-transparent px-3 py-2 dark:border-zinc-600";
  return <div className="space-y-5">
    {error && <Alert>{error}</Alert>}
    <form onSubmit={login} className="space-y-3">
      <label className="block text-sm">Ссылка подписки
        <input aria-label="Ссылка подписки" type="text" autoComplete="off" value={link} onChange={e => setLink(e.target.value)} className={inputClass} disabled={busy} required />
      </label>
      <button disabled={busy} className="w-full rounded-xl bg-brand-600 px-4 py-3 font-semibold text-white disabled:opacity-50">{busy ? "Подождите…" : "Продолжить"}</button>
    </form>
    <div className="border-t border-gray-200 pt-4 dark:border-zinc-700">
      <p className="text-sm">Нет ссылки? Получите её до оплаты. Неоплаченная ссылка действует 48 часов. Почта и телефон не нужны.</p>
      <p className="mt-2 text-xs"><Link href="/privacy" className="underline">Политика обработки данных</Link></p>
      {!generated && <button type="button" onClick={create} disabled={busy} className="mt-3 w-full rounded-xl border px-4 py-3 font-semibold disabled:opacity-50">Получить ссылку подписки</button>}
      {generated && <div className="mt-3 space-y-3" role="status">
        <p className="text-sm">Сохраните ссылку: она даёт доступ к вашей подписке. Не передавайте её другим.</p>
        <label className="block text-sm">Ваша ссылка<input aria-label="Ваша ссылка" value={generated.subscription_url} readOnly onFocus={e => e.target.select()} className={inputClass} /></label>
        <p className="text-xs">До оплаты действует до {new Date(generated.expires_at).toLocaleString("ru-RU")}. Уже начатые платежи проверяются до удаления.</p>
        <button type="button" className="rounded-lg border px-4 py-2" onClick={async () => {
          try { await navigator.clipboard.writeText(generated.subscription_url); setCopied(true); }
          catch { setError("Не удалось скопировать автоматически. Выделите ссылку и скопируйте вручную."); }
        }}>{copied ? "Скопировано" : "Скопировать ссылку"}</button>
        <p className="text-xs">Вставьте сохранённую ссылку в поле выше, чтобы продолжить.</p>
      </div>}
    </div>
  </div>;
}
export function LoginForm() {
  const params = useSearchParams();
  return <section className="mx-auto max-w-md px-4 py-16"><h1 className="mb-6 text-2xl font-bold">Вход по ссылке подписки</h1><LinkAccess next={safeNextPath(params.get("next"))} /><Link href="/" className="mt-6 block underline">На главную</Link></section>;
}
export function LoginPage() { return <Suspense fallback={<p>Загрузка…</p>}><LoginForm /></Suspense>; }
