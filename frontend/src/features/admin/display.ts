/** Даты админки всегда в московском времени, независимо от устройства оператора. */
export function adminDate(value: string | null | undefined): string {
  if (!value) return "Нет данных";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Нет данных";
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Moscow" }).format(date);
}
