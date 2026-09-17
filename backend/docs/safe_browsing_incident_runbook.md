# Runbook: предупреждение Safari «мошеннический сайт»

## Назначение

Этот runbook применяется, когда Safari/Chrome показывает красный interstitial для
`bravada-connect.ru`. Предупреждение создаёт внешний список репутации; его нельзя снять
редиректом или скрыть интерфейсом сайта. Сначала исключается компрометация, затем владелец
домена запрашивает пересмотр классификации.

## 1. Не обходить защиту

- Не предлагать пользователям отключать Fraudulent Website Warning.
- Не переходить через interstitial для диагностики: проверять сайт `curl`, Search Console
  URL Inspection и изолированными сканерами.
- Не подавать повторные review, пока предыдущий запрос рассматривается.

## 2. Техническая проверка

Выполнить с доверенной рабочей станции:

```bash
curl -sSIL http://bravada-connect.ru/
curl -sSIL https://www.bravada-connect.ru/
curl -sS https://bravada-connect.ru/.well-known/security.txt
curl -sS https://bravada-connect.ru/ | grep -E 'google-site-verification|Content-Security-Policy'
echo | openssl s_client -connect bravada-connect.ru:443 -servername bravada-connect.ru 2>/dev/null \
  | openssl x509 -noout -issuer -subject -dates -ext subjectAltName
cd frontend && npm audit --omit=dev --audit-level=high
```

Ожидается:

- HTTP и `www` дают `301` на `https://bravada-connect.ru/...`;
- TLS валиден для apex и `www`;
- CSP разрешает скрипты только с собственного origin;
- в HTML есть verification meta, если токен задан в production `.env`;
- production dependency audit не содержит high/critical;
- security.txt отдаётся с Contact, Canonical и неистёкшим Expires.

Также проверить GitLab deploy SHA, список изменённых файлов, незнакомые URL в sitemap и
Security Issues в Google Search Console. Если есть пример вредоносного URL, сначала удалить
контент/бекдор и закрыть вектор атаки; review до очистки не подавать.

## 3. Deploy исправления

После зелёного MR выполнить штатный manual job `deploy` для merge-коммита. Проверить
`nginx -t` в job и повторить HTTP/TLS probes. Не печатать значение
`NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION`: достаточно проверить наличие метатега в HTML.

## 4. Google Search Console / Safe Browsing

1. Открыть property `https://bravada-connect.ru/` или Domain property.
2. Перейти в **Security & Manual Actions → Security issues**.
3. Изучить каждый пример URL и подтвердить, что он очищен.
4. Нажать **Request Review**, описать устранённые причины: обновление зависимостей,
   проверка отсутствия внешних скриптов/редиректов, canonical host, TLS и security contact.
5. Если Search Console не показывает issue, отправить false-positive через официальный
   Google Safe Browsing `report_error`.

## 5. Apple Website Review

Если Google уже считает сайт безопасным, а предупреждение остаётся только в Safari, отправить
отдельный запрос через `https://websitereview.apple.com/report-classification-error/`.
Форма требует URL, CAPTCHA и опционально email/comment. CAPTCHA решает оператор вручную.

Рекомендуемый комментарий:

> Legitimate VPN subscription service owned by a registered individual entrepreneur.
> We verified TLS, canonical redirects, CSP, first-party scripts, public legal contacts and
> dependencies; no malware, credential impersonation or unauthorized redirects were found.
> The site uses passwordless email OTP only for its own customer accounts. Please review the
> fraudulent-website classification for https://bravada-connect.ru/.

## 6. Контроль после review

- Зафиксировать дату и канал запроса, не публикуя email/идентификаторы обращения.
- Проверять статус не чаще одного раза в сутки.
- После снятия флага проверить Safari на отдельной сети/устройстве и Chrome Safe Browsing.
- Если через несколько дней Apple не обновила классификацию при чистом Google-статусе,
  приложить результаты проверок в Apple Feedback Assistant.
