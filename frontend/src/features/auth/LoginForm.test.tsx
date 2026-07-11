import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { authApi } = vi.hoisted(() => ({
  authApi: {
    sendCode: vi.fn(),
    verifyCode: vi.fn(),
  },
}));

const { useSearch } = vi.hoisted(() => ({
  useSearch: () => new URLSearchParams("next=/dashboard"),
}));

vi.mock("@/features/auth/api", () => ({ authApi }));
vi.mock("@/shared/hooks/useCountdown", () => ({
  useCountdown: () => ({ seconds: 0, start: vi.fn(), isRunning: false }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: useSearch,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/login",
  useParams: () => ({}),
}));

import { LoginForm } from "./LoginForm";

const originalLocation = window.location;

beforeEach(() => {
  authApi.sendCode.mockReset();
  authApi.verifyCode.mockReset();
  Object.defineProperty(window, "location", {
    value: { ...originalLocation, href: "" },
    writable: true,
    configurable: true,
  });
});

describe("LoginForm", () => {
  it("shows a validation error for an empty email", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.click(screen.getByRole("button", { name: "Получить код" }));
    expect(await screen.findByText("Введите email")).toBeInTheDocument();
  });

  it("shows a validation error for an invalid email", async () => {
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/Email/i), "abc");
    // type=email + нативная constraint-validation блокируют JS-submit в happy-dom;
    // отправляем форму напрямую, чтобы сработал zod-resolver RHF.
    const form = screen
      .getByRole("button", { name: "Получить код" })
      .closest("form") as HTMLFormElement;
    fireEvent.submit(form);
    expect(
      await screen.findByText("Введите корректный email"),
    ).toBeInTheDocument();
  });

  it("moves to the code step on a successful code send", async () => {
    authApi.sendCode.mockResolvedValue({ ok: true, data: { ttl_minutes: 10 } });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/Email/i), "user@example.com");
    await user.click(screen.getByRole("button", { name: "Получить код" }));
    expect(
      await screen.findByText(/Код подтверждения отправлен/),
    ).toBeInTheDocument();
  });

  it("shows the disposable-email error", async () => {
    authApi.sendCode.mockResolvedValue({ ok: false, error: "disposable_email" });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/Email/i), "user@temp.mail");
    await user.click(screen.getByRole("button", { name: "Получить код" }));
    expect(await screen.findByText(/временной почтой/)).toBeInTheDocument();
  });

  it("verifies the code and redirects to next on success", async () => {
    authApi.sendCode.mockResolvedValue({ ok: true, data: { ttl_minutes: 10 } });
    authApi.verifyCode.mockResolvedValue({ ok: true, data: {} });
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/Email/i), "user@example.com");
    await user.click(screen.getByRole("button", { name: "Получить код" }));
    const codeInput = await screen.findByLabelText(/Код подтверждения/i);
    await user.type(codeInput, "123456");
    await user.click(screen.getByRole("button", { name: "Войти" }));
    await waitFor(() => {
      expect(window.location.href).toBe("/dashboard");
    });
  });
});
