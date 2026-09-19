-- Единая выборка пользователей для админской статистики. Исходные аккаунты и
-- атрибуция сохраняются: накрученная кампания исключается только при чтении аналитики.
CREATE OR REPLACE VIEW analytics_eligible_user_ids AS
SELECT i.internal_user_id
FROM user_identities i
WHERE NOT i.is_internal
  AND NOT EXISTS (
      SELECT 1
      FROM telegram_ads_attributions a
      WHERE a.internal_user_id = i.internal_user_id
        AND a.campaign_key = 'nakrut'
  );
