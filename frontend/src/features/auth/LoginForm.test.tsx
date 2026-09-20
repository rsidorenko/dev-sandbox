import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { authApi, userApi, push } = vi.hoisted(() => ({
  authApi: { requestLink: vi.fn() },
  userApi: { profile: vi.fn() },
  push: vi.fn(),
}));

const { useSearch, setSearch } = vi.hoisted(() => {
  let qs = "next=/dashboard";
  return {
    useSearch: () => new URLSearchParams(qs),
    setSearch: (next: string) => {
      qs = next;
    },
  };
});

vi.mock("@/features/auth/api", () => ({ authApi }));
vi.mock("@/entities/user/api", () => ({ userApi }));
vi.mock("next/navigation", () => ({
  useSearchParams: useSearch,
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/login",
  useParams: () => ({}),
}));

import { LoginForm } from "./LoginForm";
import { clearToken, getToken } from "@/shared/lib/tokenStore";

// Формат как у secrets.token_urlsafe(16) — см. tokenStore.TOKEN_RE.
const TOKEN = "e2e-token-1234567890abc";
const SUB_LINK = `https://bravada-connect.ru/sub/${TOKEN}`;

beforeEach(() => {
  setSearch("next=/dashboard");
  authApi.requestLink.mockReset();
  userApi.profile.mockReset();
  push.mockReset();
  clearToken();
});

describe("LoginForm (вход по ссылке)", () => {
  it("shows a validation error for an empty input", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByText("Вставьте ссылку или токен")).toBeInTheDocument();
  });

  it("rejects text that is not a Bravada link/token without calling the API", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/Ссылка или токен/i), "hello world");
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByText(/не похоже на ссылку/i)).toBeInTheDocument();
    expect(userApi.profile).not.toHaveBeenCalled();
    expect(getToken()).toBeNull();
  });

  it("logs in with a pasted subscription link and navigates to next", async () => {
    userApi.profile.mockResolvedValue({ ok: true, data: { user: {}, subscription: null } });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/Ссылка или токен/i), SUB_LINK);
    await user.click(screen.getByRole("button", { name: "Войти" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard"));
    expect(getToken()).toBe(TOKEN);
  });

  it("accepts an /auth/<token> link and extracts the token", async () => {
    userApi.profile.mockResolvedValue({ ok: true, data: { user: {}, subscription: null } });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(
      screen.getByLabelText(/Ссылка или токен/i),
      `https://bravada-connect.ru/auth/${TOKEN}?next=/trial`,
    );
    await user.click(screen.getByRole("button", { name: "Войти" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(getToken()).toBe(TOKEN);
  });

  it("clears the token and shows an error when the link is invalid on the server", async () => {
    userApi.profile.mockResolvedValue({ ok: false, error: "unauthorized" });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/Ссылка или токен/i), SUB_LINK);
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByText(/недействительна или отозвана/i)).toBeInTheDocument();
    expect(getToken()).toBeNull();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("LoginForm (заявка ссылки на почту)", () => {
  it("shows a validation error for an empty email", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.click(screen.getByRole("button", { name: "Получить на почту" }));
    await user.click(screen.getByRole("button", { name: "Получить ссылку" }));
    expect(await screen.findByText("Введите email")).toBeInTheDocument();
  });

  it("sends the request with next and shows the 'check your email' state", async () => {
    authApi.requestLink.mockResolvedValue({ ok: true, data: { ok: true, sent: true } });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.click(screen.getByRole("button", { name: "Получить на почту" }));
    await user.type(screen.getByLabelText(/^Email$/i), "user@example.com");
    await user.click(screen.getByRole("button", { name: "Получить ссылку" }));
    expect(await screen.findByText("Проверьте почту")).toBeInTheDocument();
    expect(authApi.requestLink).toHaveBeenCalledWith(
      "user@example.com",
      undefined,
      "/dashboard",
    );
  });

  it("shows the disposable-email error", async () => {
    authApi.requestLink.mockResolvedValue({ ok: false, error: "disposable_email" });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.click(screen.getByRole("button", { name: "Получить на почту" }));
    await user.type(screen.getByLabelText(/^Email$/i), "user@temp.mail");
    await user.click(screen.getByRole("button", { name: "Получить ссылку" }));
    expect(await screen.findByText(/временной почтой/)).toBeInTheDocument();
  });
});
