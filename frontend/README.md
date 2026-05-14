# Bravada — посадочная страница

Production-ready посадочная страница для Telegram-бота, продающего подписку на услугу защищённого сетевого доступа.

## Стек

- **Next.js 14** (App Router)
- **TypeScript**
- **Tailwind CSS**

## Установка

```bash
npm install
```

## Запуск локально

```bash
npm run dev
```

Сайт будет доступен на [http://localhost:3000](http://localhost:3000).

## Сборка для продакшена

```bash
npm run build
npm start
```

## Архитектура

Лендинг **не создаёт платежи** — он только ведёт пользователя в Telegram-бота. Весь платёжный цикл и выдача доступа реализованы в backend бота.

```
┌───────────────┐
│   Лендинг     │  Пользователь нажимает «Оплатить»
│   (Next.js)   │  → переход в Telegram-бота
└───────┬───────┘
        │  https://t.me/{BOT_USERNAME}?start=buy
        ▼
┌───────────────┐
│ Telegram-бот  │  Пользователь выбирает тариф и количество устройств
│               │  → бот формирует signed checkout URL
└───────┬───────┘
        │  Redirect на внешнюю страницу оплаты
        ▼
┌───────────────┐
│  Платёжный    │  Пользователь оплачивает (карта, СБП, ...)
│  провайдер    │  → webhook на backend бота
└───────┬───────┘
        │  POST /billing/fulfillment/webhook
        ▼
┌───────────────┐
│ Backend бота  │  Проверяет подпись webhook
│               │  → активирует подписку
│               │  → отправляет настройки в Telegram
└───────────────┘
```

### Что делает лендинг

- Показывает тарифы, FAQ, юридические страницы
- Кнопки «Оплатить» ведут в Telegram-бота: `https://t.me/{botUsername}?start=buy`
- Страницы `/success` и `/fail` — для возврата после оплаты

### Что делает backend бота

- Управляет пользователями, подписками, рефералами
- Формирует signed checkout URL для оплаты
- Принимает webhook от платёжного провайдера (`/billing/fulfillment/webhook`)
- Выдаёт настройки VLESS-доступа
- Исходный код: `TelegramBotVPN/backend/`

## Тарифы

Тарифы синхронизированы с backend: `TelegramBotVPN/backend/src/app/domain/plans.py`

| ID тарифа | Название | Срок | Базовая цена | Устройства по умолчанию |
|---|---|---|---|---|
| `1m` | 1 месяц | 30 дней | 300 ₽ | 5 |
| `3m` | 3 месяца | 90 дней | 750 ₽ | 5 |
| `6m` | 6 месяцев | 180 дней | 1 350 ₽ | 5 |

Дополнительные устройства: 80 ₽ за каждое сверх лимита, в месяц.

Если в backend тарифы изменились — обновите `src/config/site.ts`.

## Переменные окружения

```bash
cp .env.example .env.local
```

| Переменная | Описание |
|---|---|
| `NEXT_PUBLIC_BASE_URL` | Базовый URL вашего сайта (например, `https://example.com`) |

**Важно:** лендинг не хранит секреты платёжных систем. Все секреты находятся в backend бота.

## Где заменить данные

Откройте файл `src/config/site.ts` и замените placeholder-значения:

```ts
export const siteConfig = {
  ipName: "ИП Иванов Иван Иванович",       // ← ваше ФИО
  inn: "000000000000",                       // ← ваш ИНН
  supportEmail: "support@example.com",       // ← ваш email
  supportTelegram: "https://t.me/your_support_bot",  // ← ссылка
  supportTelegramHandle: "@your_support_bot",         // ← handle
  botUsername: "your_bot_username",                    // ← username бота без @
  siteUrl: "https://your-domain.com",                 // ← ваш домен
};
```

## Что проверить перед запуском

- [ ] Заменены все placeholder-данные в `src/config/site.ts`
- [ ] `botUsername` совпадает с `BOT_USERNAME` в backend бота
- [ ] Домен в `robots.txt`, `sitemap.ts`, `.env` заменён на реальный
- [ ] Тарифы в `site.ts` совпадают с `plans.py` в backend
- [ ] В backend настроен `TELEGRAM_STOREFRONT_CHECKOUT_URL` — URL внешней страницы оплаты
- [ ] В backend настроен webhook `PAYMENT_FULFILLMENT_WEBHOOK_SECRET`
- [ ] Favicon заменён с placeholder на реальный (`public/favicon.ico`)

## Структура проекта лендинга

```
src/
├── app/
│   ├── contacts/          # Контакты и реквизиты
│   ├── fail/              # Ошибка оплаты (return URL)
│   ├── offer/             # Публичная оферта
│   ├── privacy/           # Политика конфиденциальности
│   ├── refund/            # Условия возврата
│   ├── success/           # Успешная оплата (return URL)
│   ├── globals.css
│   ├── layout.tsx         # Корневой layout с SEO
│   ├── page.tsx           # Главная страница
│   └── sitemap.ts         # Автогенерация sitemap
├── components/
│   ├── CtaSection.tsx
│   ├── DeliverySection.tsx
│   ├── FaqSection.tsx
│   ├── Header.tsx
│   ├── HeroSection.tsx
│   ├── HowItWorks.tsx
│   ├── PageHeader.tsx
│   └── TariffsSection.tsx
└── config/
    └── site.ts            # Все настраиваемые данные
```

## Папка TelegramBotVPN/ (НЕ ИЗМЕНЯТЬ)

Содержит исходный код backend бота. Файлы в этой папке используются только как источник данных для синхронизации тарифов и интеграционных деталей с лендингом. Не изменяйте, не перемещайте и не удаляйте файлы внутри `TelegramBotVPN/`.
