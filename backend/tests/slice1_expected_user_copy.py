"""Expected user-facing copy for slice-1 catalog tests (align with message_catalog._CATALOG_TEXT)."""

IDENTITY_READY_TEXT = (
    "Добро пожаловать! Ваш чат подключён.\n"
    "Используйте /menu для просмотра тарифов и оформления подписки.\n"
    "Используйте /my_subscription, чтобы проверить текущий статус."
)

NEEDS_ONBOARDING_TEXT = (
    "Отправьте /start для регистрации, затем вы сможете использовать /status или /help. "
    "Бот должен распознать этот чат, прежде чем показать информацию о доступе."
)

INACTIVE_OR_NOT_ELIGIBLE_TEXT = (
    "Доступ для этого аккаунта сейчас недоступен. Если вы здесь впервые, отправьте /start, затем /status или /help. "
    "Эта версия не предоставляет новый доступ и не отправляет файлы."
)

SLICE1_HELP_TEXT = (
    "Доступные команды:\n"
    "/start - подключить чат\n"
    "/menu - главное меню\n"
    "/plans - доступные тарифы\n"
    "/buy - оформить подписку\n"
    "/checkout - аналог /buy\n"
    "/success - что делать после оплаты\n"
    "/my_subscription - статус подписки (аналог /status)\n"
    "/status - статус подписки\n"
    "/renew - продлить подписку\n"
    "/support - помощь и FAQ\n"
    "/support_contact - контакты поддержки\n"
    "/resend_access - повторно получить инструкции доступа\n"
    "/get_access - аналог /resend_access\n"
    "/help - эта справка"
)

RESEND_ACCESS_ACCEPTED_TEXT = (
    "Запрос на получение инструкций доступа принят. Если доставка доступна, инструкции будут отправлены повторно."
)

RESEND_ACCESS_NOT_ENABLED_TEXT = "Эта функция пока недоступна."

RESEND_ACCESS_NOT_ELIGIBLE_TEXT = (
    "Инструкции доступа нельзя повторно отправить для этого аккаунта.\n"
    "Если подписка неактивна или истекла, используйте /renew."
)

RESEND_ACCESS_COOLDOWN_TEXT = "Подождите немного перед повторным запросом инструкций доступа."

RESEND_ACCESS_NOT_READY_TEXT = "Инструкции доступа ещё не готовы для повторной отправки. Попробуйте позже."

RESEND_ACCESS_TEMPORARILY_UNAVAILABLE_TEXT = "Повторная отправка инструкций временно недоступна. Попробуйте позже."

SUBSCRIPTION_ACTIVE_ACCESS_NOT_READY_TEXT = "Ваша подписка активна до {date}."

SUBSCRIPTION_ACTIVE_ACCESS_READY_TEXT = "Ваша подписка активна до {date}."

TELEGRAM_COMMAND_RATE_LIMITED_TEXT = "Слишком много запросов. Пожалуйста, попробуйте позже."
