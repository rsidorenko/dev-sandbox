-- Кнопки под сообщением рассылки (inline keyboard).
-- buttons = JSONB-массив кнопок в формате Telegram: каждый элемент {text, callback_data}
-- для действий бота либо {text, url} для ссылок. Пустой массив = без кнопок.
-- Markup строится в admin_broadcast._build_reply_markup (1 кнопка на строку inline_keyboard).
ALTER TABLE broadcast_jobs
    ADD COLUMN IF NOT EXISTS buttons JSONB NOT NULL DEFAULT '[]'::jsonb;
