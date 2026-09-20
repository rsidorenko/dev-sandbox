-- Дроп email-таблиц после ухода от email-аутентификации (link-auth, MR !154-!161).
-- Вход на сайт и конфиги — по subscription_token (Bearer, без кук); почта с MR4 —
-- только канал доставки первой ссылки и нигде не хранится. Прощальная рассылка
-- со ссылками-подписками отправлена 2026-09-20 (единственный web-юзер с почтой
-- без Telegram получил ссылку). email_verification_codes (023) не имеет FK на
-- user_emails — порядок дропа свободен; user_emails держит FK только НА
-- user_identities, внешних ссылок на обе таблицы нет.
DROP TABLE IF EXISTS email_verification_codes;
DROP TABLE IF EXISTS user_emails;
