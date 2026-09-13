import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AdminUser } from "@/entities/admin/types";
import { adminApi } from "./api";
import { ResetTestAccountModal } from "./TestAccountModals";

const user: AdminUser = {
  internal_user_id: "u123456",
  telegram_user_id: 123456,
  email: null,
  trial_used: true,
  is_internal: true,
  subscription_state: "active",
  is_active: true,
  active_until: null,
  remaining_days: 2,
  plan_id: "trial",
  device_count: 1,
  balance_rubles: 0,
};

afterEach(() => vi.restoreAllMocks());

describe("ResetTestAccountModal", () => {
  it("разблокирует сброс только после точного Telegram ID и причины", async () => {
    const reset = vi.spyOn(adminApi, "resetTestAccount").mockResolvedValue({
      ok: true,
      data: {
        telegram_user_id: 123456,
        internal_user_id: "u123456",
        vpn_outcome: "success",
        deleted_rows: { user_identities: 1 },
        correlation_id: "corr-1",
      },
    });
    const onReset = vi.fn();
    render(<ResetTestAccountModal user={user} onClose={vi.fn()} onReset={onReset} />);

    const submit = screen.getByRole("button", { name: "Удалить и сбросить" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Введите Telegram ID/), { target: { value: "12345" } });
    fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "Повторный trial flow" } });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Введите Telegram ID/), { target: { value: "123456" } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(reset).toHaveBeenCalledWith("u123456", 123456, "Повторный trial flow"));
    expect(onReset).toHaveBeenCalledWith(expect.objectContaining({ correlation_id: "corr-1" }));
  });
});
