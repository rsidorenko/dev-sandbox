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
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    expect(userApi.profile).not.toHaveBeenCalled();
  });

  it("rejects text that is not a Bravada link/token without calling the API", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/^Ссылка подписки$/i), "hello world");
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    expect(await screen.findByText(/Вставьте полную ссылку/i)).toBeInTheDocument();
    expect(userApi.profile).not.toHaveBeenCalled();
    expect(getToken()).toBeNull();
  });

  it("logs in with a pasted subscription link and navigates to next", async () => {
    userApi.profile.mockResolvedValue({ ok: true, data: { user: {}, subscription: null } });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/^Ссылка подписки$/i), SUB_LINK);
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard"));
    expect(getToken()).toBe(TOKEN);
  });

  it("accepts an /auth/<token> link and extracts the token", async () => {
    userApi.profile.mockResolvedValue({ ok: true, data: { user: {}, subscription: null } });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(
      screen.getByLabelText(/^Ссылка подписки$/i),
      `https://bravada-connect.ru/auth/${TOKEN}?next=/trial`,
    );
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(getToken()).toBe(TOKEN);
  });

  it("clears the token and shows an error when the link is invalid on the server", async () => {
    userApi.profile.mockResolvedValue({ ok: false, error: "unauthorized" });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/^Ссылка подписки$/i), SUB_LINK);
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    expect(await screen.findByText(/недействительна, отозвана/i)).toBeInTheDocument();
    expect(getToken()).toBeNull();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("Получение ссылки на экране", () => {
  it("показывает ссылку без email и копирует её", async () => {
    authApi.requestLink.mockResolvedValue({ok:true,data:{ok:true,subscription_url:SUB_LINK,expires_at:"2026-10-02T12:00:00Z"}});
    const user = userEvent.setup();
    render(<LoginForm />);
    expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button",{name:"Получить ссылку подписки"}));
    expect(await screen.findByLabelText("Ваша ссылка")).toHaveValue(SUB_LINK);
    expect(getToken()).toBeNull();
    await user.click(screen.getByRole("button",{name:"Скопировать ссылку"}));
    expect(await navigator.clipboard.readText()).toBe(SUB_LINK);
    expect(authApi.requestLink).toHaveBeenCalledWith(undefined);
  });
});
