import { test, expect } from "@playwright/test";

test("ручной инцидент проходит работу и закрытие с историей", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  const incidents: { id: string; title: string; status: string; version: number; created_at: string; created_by: string }[] = [];
  const events: { id: number; incident_id: string; actor: string; action: string; note: string; created_at: string }[] = [];
  const at = "2026-09-23T10:00:00Z";
  await page.route("**/api/v1/**", route => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = request.postDataJSON();
      if (new URL(request.url()).pathname.endsWith("/incidents")) {
        incidents.push({ id: body.request_id, title: body.title, status: "open", version: 1, created_at: at, created_by: "admin" });
        events.push({ id: 1, incident_id: body.request_id, actor: "admin", action: "create", note: body.note, created_at: at });
      } else {
        incidents[0].status = body.action === "ack" ? "ack" : body.action === "resolve" ? "resolved" : incidents[0].status;
        incidents[0].version++;
        events.push({ id: events.length + 1, incident_id: incidents[0].id, actor: "admin", action: body.action, note: body.note, created_at: at });
      }
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true }) });
    }
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, promocodes: [], observed_at: at, total: 0, issues: [], incidents, events, audit: [] }) });
  });
  await page.goto("/admin/incidents");
  await page.getByText("Создать инцидент по обращению", { exact: true }).click();
  await page.getByRole("textbox", { name: "Название инцидента" }).fill("Проверка сети провайдера");
  await page.getByRole("textbox", { name: "Описание инцидента" }).fill("Поступило обращение");
  await page.getByRole("button", { name: "Создать", exact: true }).click();
  await page.getByRole("button", { name: "Взять в работу" }).click();
  await expect(page.getByText("В работе", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Комментарий: Проверка сети провайдера" }).fill("Соединение восстановлено, проверено с устройства");
  await page.getByRole("button", { name: "Закрыть с результатом" }).click();
  await page.getByRole("checkbox", { name: "Показать закрытые" }).check();
  await expect(page.getByText("Закрыт", { exact: true }).first()).toBeVisible();
  await page.getByText("История · последние 50 событий").click();
  await expect(page.getByText("Соединение восстановлено, проверено с устройства", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("повтор после потери ответа использует тот же идентификатор запроса", async ({ context, page }) => {
  await context.addCookies([{ name: "admin_session", value: "test-session", domain: "localhost", path: "/" }]);
  const ids: string[] = [];
  await page.route("**/api/v1/**", route => {
    if (route.request().method() === "POST") {
      ids.push(route.request().postDataJSON().request_id);
      if (ids.length === 1) return route.abort("failed");
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, duplicate: true }) });
    }
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, promocodes: [], observed_at: "2026-09-23T10:00:00Z", total: 0, issues: [], incidents: [], events: [], audit: [] }) });
  });
  await page.goto("/admin/incidents");
  await page.getByText("Создать инцидент по обращению", { exact: true }).click();
  await page.getByRole("textbox", { name: "Название инцидента" }).fill("Проверка сети");
  await page.getByRole("button", { name: "Создать", exact: true }).click();
  await expect(page.getByText(/Не удалось сохранить/)).toBeVisible();
  await page.getByRole("button", { name: "Создать", exact: true }).click();
  await expect(page.getByText("Этот сигнал уже зарегистрирован.", { exact: true })).toBeVisible();
  expect(ids).toHaveLength(2);
  expect(ids[0]).toBe(ids[1]);
});
