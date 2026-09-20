/**
 * Токен подписки — единственный креденшл пользователя (link-auth).
 *
 * Токен живёт ТОЛЬКО в памяти модуля: никакие куки/localStorage не ставятся
 * (осознанный уход от cookies и почт как персональных данных). Следствия:
 * SPA-навигация сессию сохраняет, полная перезагрузка страницы = выход
 * (пользователь входит повторно по ссылке). Pub/sub нужен, чтобы AuthProvider
 * и компоненты реагировали на вход/выход без перезагрузки страницы.
 */

export type TokenListener = () => void;

let currentToken: string | null = null;
const listeners = new Set<TokenListener>();

export function getToken(): string | null {
  return currentToken;
}

export function setToken(token: string): void {
  currentToken = token;
  for (const listener of listeners) listener();
}

export function clearToken(): void {
  if (currentToken === null) return;
  currentToken = null;
  for (const listener of listeners) listener();
}

export function subscribeToken(listener: TokenListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Формат токена: secrets.token_urlsafe(16) — [A-Za-z0-9_-], обычно 22 символа. */
const TOKEN_RE = /^[A-Za-z0-9_-]{16,128}$/;

export function isValidTokenFormat(token: string): boolean {
  return TOKEN_RE.test(token);
}

/**
 * Вытащить токен из вставленного пользователем текста: полной ссылки
 * (`https://site/sub/<tok>`, `https://site/auth/<tok>?next=…`), пути
 * (`/sub/<tok>`) или «голого» токена. Точка не допускается — JWT-подобные
 * строки отсекаются ещё и на бэкенде.
 */
export function parseLoginToken(raw: string): string | null {
  const input = raw.trim();
  if (!input) return null;
  const fromUrl = input.match(/\/(?:sub|auth)\/([A-Za-z0-9_-]{16,128})/);
  if (fromUrl) return fromUrl[1];
  if (isValidTokenFormat(input)) return input;
  return null;
}
