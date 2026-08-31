import { describe, expect, it } from "vitest";
import { sanitizeHtml } from "./sanitizeHtml";

describe("sanitizeHtml", () => {
  it("пропускает разрешённые теги как есть", () => {
    expect(sanitizeHtml("<b>жирный</b> и <i>курсив</i>")).toBe(
      "<b>жирный</b> и <i>курсив</i>",
    );
    expect(sanitizeHtml("<pre><code>x = 1</code></pre>")).toBe(
      "<pre><code>x = 1</code></pre>",
    );
  });

  it("конвертирует перевод строки в <br>", () => {
    expect(sanitizeHtml("строка1\nстрока2")).toBe("строка1<br>строка2");
    expect(sanitizeHtml("a\r\nb")).toBe("a<br>b");
  });

  it("вырезает script целиком, вместе с содержимым", () => {
    expect(sanitizeHtml("до<script>alert(1)</script>после")).toBe("допосле");
  });

  it("вырезает svg/iframe с содержимым (без парсинга внутрь)", () => {
    expect(sanitizeHtml('<svg onload="x"><circle/></svg>ок')).toBe("ок");
    expect(sanitizeHtml('<iframe src="https://evil"></iframe>ok')).toBe("ok");
  });

  it("разворачивает незнакомые теги, сохраняя текст", () => {
    expect(sanitizeHtml("<div>привет</div>")).toBe("привет");
    expect(sanitizeHtml("<table><tr><td>ячейка</td></tr></table>")).toBe(
      "ячейка",
    );
  });

  it("выбрасывает img-тег с onerror (без содержимого)", () => {
    expect(sanitizeHtml('до<img src=x onerror="steal()">после')).toBe("допосле");
  });

  it("оставляет <a> только с http/https href и добавляет rel/target", () => {
    const out = sanitizeHtml('<a href="https://example.com">сайт</a>');
    expect(out).toContain('href="https://example.com"');
    expect(out).toContain('rel="noopener noreferrer nofollow"');
    expect(out).toContain('target="_blank"');
    expect(out).toContain("сайт");
  });

  it("убивает javascript:-href, ссылка остаётся текстом", () => {
    const out = sanitizeHtml('<a href="javascript:alert(1)">клик</a>');
    expect(out).not.toContain("javascript");
    expect(out).toContain("клик");
  });

  it("снимает все атрибуты (вкл. on*) у разрешённых тегов", () => {
    expect(sanitizeHtml('<b onclick="x()" class="y">текст</b>')).toBe(
      "<b>текст</b>",
    );
    expect(sanitizeHtml('<u style="color:red">u</u>')).toBe("<u>u</u>");
  });

  it("экранирует голый HTML-текст безопасно", () => {
    expect(sanitizeHtml("a < b")).toContain("&lt;");
  });

  it("пустой ввод → пустая строка", () => {
    expect(sanitizeHtml("")).toBe("");
    expect(sanitizeHtml(null)).toBe("");
    expect(sanitizeHtml(undefined)).toBe("");
  });

  it("текст из двух строк с тегами сохраняет и то и другое", () => {
    expect(sanitizeHtml("<b>Акция!</b>\nДо пятницы")).toBe(
      "<b>Акция!</b><br>До пятницы",
    );
  });
});
