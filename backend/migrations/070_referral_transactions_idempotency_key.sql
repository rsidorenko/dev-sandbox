-- Separate the referral-transaction dedup key from the user-facing description.
--
-- Historically `description` served two roles at once: (1) the human-readable label shown
-- in the cabinet "Последние начисления", and (2) the idempotency/dedup key (unique index
-- uq_referral_tx_user_desc from migrations 048/054). That forced the crediting code to stuff
-- internal webhook data into `description` — e.g. fulfillment_processor wrote
--   webhook:l{level}:{payer_internal_user_id}:{plan_id}:{payment_amount_kopecks}:{correlation_id}
-- and balance_payment_service wrote similar composites. The web API returned `description`
-- verbatim, so a referrer could see another user's internal id and payment amount. (See
-- frontend/src/features/referral/ReferralCard.tsx "Последние начисления".)
--
-- This migration:
--   1. adds a dedicated `idempotency_key` column (the dedup key, never user-facing);
--   2. backfills it from the existing `description` (preserves dedup continuity 1:1);
--   3. moves the UNIQUE constraint onto (internal_user_id, idempotency_key);
--   4. drops the UNIQUE constraint on `description` — it becomes a clean, repeatable label;
--   5. scrubs legacy leaky `description` values (dedup no longer depends on them; the API
--      derives display labels from transaction_type/plan/level regardless).

ALTER TABLE referral_transactions
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

-- 1:1 backfill from the legacy dedup-as-description value (uq_referral_tx_user_desc kept
-- (internal_user_id, description) unique, so this yields unique (user, idempotency_key)).
UPDATE referral_transactions
SET idempotency_key = description
WHERE idempotency_key IS NULL AND description IS NOT NULL;

-- Rows with NULL description (NULLs are distinct under the old unique index, so duplicates
-- could exist): fall back to the PK transaction_id, which is globally unique.
UPDATE referral_transactions
SET idempotency_key = transaction_id
WHERE idempotency_key IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_tx_user_idemp
    ON referral_transactions (internal_user_id, idempotency_key);

DROP INDEX IF EXISTS uq_referral_tx_user_desc;

-- Remove legacy leaky webhook strings. Going forward the crediting code writes clean human
-- labels; the web API never returns raw `description` (it derives the display label from
-- transaction_type + related_plan_id + level), so this is safe and completes the cleanup.
UPDATE referral_transactions SET description = NULL;
