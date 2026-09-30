import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { KeysModal } from "./KeysModal";

const { userApi } = vi.hoisted(() => ({ userApi: { keys: vi.fn(), reissueKeys: vi.fn() } }));
vi.mock("@/entities/user/api", () => ({ userApi }));

const result = (rotation_status = "idle") => ({
  ok: true,
  data: { keys: [{ label: "Test", country: "DE", flag: "", link: "vless://test" }], subscription_url: "https://example.test/sub/test", rotation_status },
});

beforeEach(() => {
  vi.resetAllMocks();
  userApi.keys.mockResolvedValue(result());
});

it("объясняет переход и блокирует новый перевыпуск во время ожидания трафика", async () => {
  userApi.reissueKeys.mockResolvedValue(result("waiting"));
  const user = userEvent.setup();
  render(<KeysModal open onClose={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "Перевыпустить ключи" }));
  expect(screen.getByText(/Старые удалятся на всех серверах/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Да, перевыпустить" }));
  expect(await screen.findByRole("status")).toHaveTextContent("первого трафика");
  expect(screen.getByRole("button", { name: "Перевыпуск выполняется" })).toBeDisabled();
});

it("загружает сохранённый статус при повторном открытии", async () => {
  userApi.keys.mockResolvedValue(result("waiting"));
  const { rerender } = render(<KeysModal open onClose={() => {}} />);
  expect(await screen.findByRole("status")).toHaveTextContent("Новые ключи готовы");
  rerender(<KeysModal open={false} onClose={() => {}} />);
  userApi.keys.mockResolvedValue(result("completed"));
  rerender(<KeysModal open onClose={() => {}} />);
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Переход завершён"));
});

it("не сообщает об успехе при ошибке создания", async () => {
  userApi.reissueKeys.mockResolvedValue({ ok: false, error: "reissue_pending" });
  const user = userEvent.setup();
  render(<KeysModal open onClose={() => {}} />);
  await user.click(await screen.findByRole("button", { name: "Перевыпустить ключи" }));
  await user.click(screen.getByRole("button", { name: "Да, перевыпустить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("не подтверждён");
  expect(screen.queryByText(/Новые ключи готовы/)).not.toBeInTheDocument();
});
