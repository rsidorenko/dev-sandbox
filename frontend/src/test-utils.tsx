/**
 * Утилиты для component/unit-тестов (Vitest + Testing Library).
 *
 * Идиоматичный паттерн Vitest: моки next/navigation и api-модулей объявляются
 * в самом тесте через vi.mock(...) — Vitest хойстит их выше импортов.
 * Здесь — переэкспорт RTL + компактные хелперы для часто повторяющихся моков.
 */
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ReactElement } from "react";

export { act, fireEvent, render, renderHook, screen, waitFor, within };

/** Имена целей Яндекс.Метрики, которые могут рассылаться из компонентов. */
export const YM_GOALS = [
  "choose_plan",
  "trial_click",
  "trial_activated",
  "start_checkout",
  "referral_open",
  "auth_gate_email",
  "auth_gate_bot",
  "purchase",
] as const;

/** Заглушка window.ym для тестов аналитики. Возвращает vi.fn-подобный колlector. */
export function installYmMock() {
  const calls: Array<{ id: number; goal: string; params?: unknown }> = [];
  const ym = (id: number, method: string, ...rest: unknown[]) => {
    if (method === "reachGoal") {
      calls.push({ id, goal: rest[0] as string, params: rest[1] });
    }
  };
  (window as unknown as { ym: unknown }).ym = ym as unknown as (
    ...args: unknown[]
  ) => void;
  return {
    calls,
    reachGoalCalls: () => calls.map((c) => c.goal),
    reset: () => calls.splice(0, calls.length),
  };
}

export type RenderResult = ReturnType<typeof render>;
export type { ReactElement };
