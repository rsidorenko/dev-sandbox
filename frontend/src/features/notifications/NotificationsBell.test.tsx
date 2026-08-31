import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  read: vi.fn(),
  readAll: vi.fn(),
  claim: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return { ...original, notificationsApi: api };
});

import { NotificationsBell } from "./NotificationsBell";
import type { WebNotification } from "./api";

function makeNotification(overrides: Partial<WebNotification> = {}): WebNotification {
  return {
    id: 1,
    kind: "broadcast",
    title: "Привет!",
    body: "<b>Акция</b>",
    payload: {},
    created_at: "2026-08-31T12:00:00Z",
    read_at: null,
    ...overrides,
  };
}

const LIST_OK = (items: WebNotification[], unread = 0, hasMore = false) => ({
  ok: true,
  data: { ok: true, items, unread_count: unread, has_more: hasMore },
});

beforeEach(() => {
  vi.clearAllMocks();
  api.readAll.mockResolvedValue({ ok: true, data: { ok: true, updated: 0 } });
  api.read.mockResolvedValue({ ok: true, data: { ok: true, updated: 1 } });
});

describe("NotificationsBell", () => {
  it("рендерит кнопку без бейджа при нуле непрочитанных", async () => {
    api.list.mockResolvedValue(LIST_OK([]));
    render(<NotificationsBell />);
    await waitFor(() => expect(api.list).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Уведомления" })).toBeInTheDocument();
    expect(screen.queryByText(/^\d+$/)).not.toBeInTheDocument();
  });

  it("показывает бейдж с числом непрочитанных", async () => {
    api.list.mockResolvedValue(LIST_OK([makeNotification()], 3));
    render(<NotificationsBell />);
    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Уведомления (3 непрочитанных)" }),
    ).toBeInTheDocument();
  });

  it("по клику открывает dropdown и помечает всё прочитанным", async () => {
    api.list.mockResolvedValue(LIST_OK([makeNotification({ title: "Рассылка дня" })], 1));
    const user = userEvent.setup();
    render(<NotificationsBell />);
    await user.click(screen.getByRole("button", { name: /Уведомления/ }));
    expect(await screen.findByText("Рассылка дня")).toBeInTheDocument();
    await waitFor(() => expect(api.readAll).toHaveBeenCalled());
  });

  it("показывает пустое состояние без уведомлений", async () => {
    api.list.mockResolvedValue(LIST_OK([]));
    const user = userEvent.setup();
    render(<NotificationsBell />);
    await user.click(screen.getByRole("button", { name: "Уведомления" }));
    expect(await screen.findByText("Пока нет уведомлений")).toBeInTheDocument();
  });

  it("рендерит broadcast-тело как санитизированный HTML", async () => {
    api.list.mockResolvedValue(
      LIST_OK([
        makeNotification({
          body: "<b>жирно</b><script>alert(1)</script>",
        }),
      ]),
    );
    const user = userEvent.setup();
    render(<NotificationsBell />);
    await user.click(screen.getByRole("button", { name: "Уведомления" }));
    await screen.findByText("жирно");
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText("жирно").tagName).toBe("B");
  });

  it("сервисное уведомление рендерится как текст с сохранением переносов", async () => {
    api.list.mockResolvedValue(
      LIST_OK([
        makeNotification({
          kind: "subscription_expiring",
          title: "Подписка заканчивается",
          body: "Первая строка\nВторая строка",
        }),
      ]),
    );
    const user = userEvent.setup();
    render(<NotificationsBell />);
    await user.click(screen.getByRole("button", { name: "Уведомления" }));
    const para = await screen.findByText(/Первая строка/);
    expect(para.className).toContain("whitespace-pre-line");
  });

  it("кнопка клейма: eligible → успех → чип «Получено»", async () => {
    api.list.mockResolvedValue(
      LIST_OK([
        makeNotification({
          payload: { bonus: { type: "days", days: 5, label: "Получить 5 дней" } },
          claim_status: "eligible",
        }),
      ]),
    );
    api.claim.mockResolvedValue({
      ok: true,
      data: { ok: true, status: "ok", days: 5 },
    });
    const user = userEvent.setup();
    render(<NotificationsBell />);
    await user.click(screen.getByRole("button", { name: "Уведомления" }));
    await user.click(await screen.findByRole("button", { name: "Получить 5 дней" }));
    expect(await screen.findByText("Получено")).toBeInTheDocument();
    expect(api.claim).toHaveBeenCalledWith(1);
  });

  it("клейм с already_claimed показывает ошибку и закрывает кнопку", async () => {
    api.list.mockResolvedValue(
      LIST_OK([
        makeNotification({
          payload: { bonus: { type: "promo", label: "Забрать промокод" } },
          claim_status: "eligible",
        }),
      ]),
    );
    api.claim.mockResolvedValue({ ok: false, error: "already_claimed" });
    const user = userEvent.setup();
    render(<NotificationsBell />);
    await user.click(screen.getByRole("button", { name: "Уведомления" }));
    await user.click(await screen.findByRole("button", { name: "Забрать промокод" }));
    expect(await screen.findByText("Бонус уже получен.")).toBeInTheDocument();
    expect(screen.getByText("Получено")).toBeInTheDocument();
  });

  it("expired-бонус рендерится чипом «Срок истёк» без кнопки", async () => {
    api.list.mockResolvedValue(
      LIST_OK([
        makeNotification({
          payload: { bonus: { type: "days", days: 5, label: "Получить 5 дней" } },
          claim_status: "expired",
        }),
      ]),
    );
    const user = userEvent.setup();
    render(<NotificationsBell />);
    await user.click(screen.getByRole("button", { name: "Уведомления" }));
    expect(await screen.findByText("Срок истёк")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Получить 5 дней" }),
    ).not.toBeInTheDocument();
  });

  it("«Показать ещё» догружает следующую страницу", async () => {
    api.list.mockImplementation(async (_limit: number, offset: number) =>
      offset === 0
        ? LIST_OK([makeNotification({ id: 1, title: "Первая" })], 1, true)
        : LIST_OK([makeNotification({ id: 2, title: "Вторая" })], 1, false),
    );
    const user = userEvent.setup();
    render(<NotificationsBell />);
    await user.click(screen.getByRole("button", { name: "Уведомления" }));
    await screen.findByText("Первая");
    await user.click(await screen.findByRole("button", { name: "Показать ещё" }));
    expect(await screen.findByText("Вторая")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Показать ещё" }),
    ).not.toBeInTheDocument();
  });

  it("закрывается по Escape", async () => {
    api.list.mockResolvedValue(LIST_OK([makeNotification({ title: "Есть новость" })], 1));
    const user = userEvent.setup();
    render(<NotificationsBell />);
    await user.click(screen.getByRole("button", { name: /Уведомления/ }));
    expect(await screen.findByText("Есть новость")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText("Есть новость")).not.toBeInTheDocument();
  });
});
