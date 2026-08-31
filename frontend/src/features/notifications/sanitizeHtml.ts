/**
 * Allowlist-санитайзер HTML-тела broadcast-уведомлений.
 *
 * Сервер уже валидирует разметку рассылки (_validate_html в admin_broadcast),
 * но тело хранится как есть — на клиенте санитайзим повторно (defense-in-depth):
 * разрешены только b/i/em/strong/u/s/code/pre/br и <a href> с http/https.
 * Запрещённые теги разворачиваются (текст остаётся), script/style выкидываются
 * с содержимым, все атрибуты (вкл. on*) снимаются. \n → <br>.
 */

const ALLOWED_TAGS = ["b", "i", "em", "strong", "u", "s", "code", "pre", "a", "br"] as const;
const ALLOWED_SELECTOR = ALLOWED_TAGS.join(",");
const NOT_ALLOWED_SELECTOR = `*:not(${ALLOWED_SELECTOR})`;
const HREF_HTTP = /^https?:\/\//i;
const DROP_WITH_CONTENT = new Set(["script", "style", "iframe", "object", "embed", "svg", "math"]);

export function sanitizeHtml(input: string | null | undefined): string {
  if (!input) return "";
  const html = input.replace(/\r\n/g, "\n").replace(/\n/g, "<br>");
  const doc = new DOMParser().parseFromString(html, "text/html");

  // 1) <a>: выживает только http/https-href (javascript:, data: и пр. снимаются).
  for (const link of Array.from(doc.body.querySelectorAll("a"))) {
    const href = (link.getAttribute("href") || "").trim();
    if (HREF_HTTP.test(href)) {
      link.setAttribute("href", href);
      link.setAttribute("target", "_blank");
      link.setAttribute("rel", "noopener noreferrer nofollow");
    }
  }
  // 2) Все атрибуты — прочь (кроме подготовленных href/target/rel у ссылок).
  for (const el of Array.from(doc.body.querySelectorAll("*"))) {
    const keep =
      el.tagName.toLowerCase() === "a" &&
      el.hasAttribute("href") &&
      HREF_HTTP.test(el.getAttribute("href") || "");
    for (const attr of Array.from(el.attributes)) {
      if (keep && (attr.name === "href" || attr.name === "target" || attr.name === "rel")) continue;
      el.removeAttribute(attr.name);
    }
  }
  // 3) Запрещённые теги: опасные — с содержимым, прочие — разворачиваем (текст остаётся).
  //    Вложенные запрещённые ловятся следующей итерацией цикла (guard от зацикливания).
  let guard = 0;
  while (guard++ < 50) {
    const bad = Array.from(doc.body.querySelectorAll(NOT_ALLOWED_SELECTOR));
    if (bad.length === 0) break;
    for (const el of bad) {
      const parent = el.parentNode;
      if (!parent) continue;
      if (DROP_WITH_CONTENT.has(el.tagName.toLowerCase())) {
        el.remove();
        continue;
      }
      while (el.firstChild) parent.insertBefore(el.firstChild, el);
      parent.removeChild(el);
    }
  }
  return doc.body.innerHTML;
}
