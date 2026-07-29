-- Расширяем аудиторию промокодов: добавляем 'all_users' — код подходит любому
-- аккаунту (и новым, и «не новым»). Прежние две аудитории сохраняются.
-- См. PromoAudience / audience_eligible (app.domain.promo).
--
-- CHECK на audience был задан inline в миграции 061 → PostgreSQL дал ему
-- стандартное имя promo_codes_audience_check. Пересоздаём с расширенным списком.
ALTER TABLE promo_codes DROP CONSTRAINT IF EXISTS promo_codes_audience_check;
ALTER TABLE promo_codes ADD CONSTRAINT promo_codes_audience_check
    CHECK (audience IN ('new_users', 'existing_users', 'all_users'));
