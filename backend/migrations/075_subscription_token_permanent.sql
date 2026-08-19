-- 075: URL подписки снова постоянный (интенция f620f73 «make subscription URL permanent»).
-- #94 (7bfd690, 2026-05-27) молча вернул 90-дневный TTL токена из #74: на 90-й день
-- после выдачи первый же бот-флоу ротировал токен, и Karing со старым URL получал
-- 404/410. NULL в subscription_token_expires_at и /sub/, и _ensure_subscription_token
-- уже трактуют как «бессрочный»; истёкшим подписчикам /sub/ отдаёт 403 по
-- entitlement-гейту, поэтому security-цель #74 не теряется.
UPDATE user_identities
   SET subscription_token_expires_at = NULL
 WHERE subscription_token_expires_at IS NOT NULL;
