-- Defensive dedup guard for referral commission credits (companion to 048).
--
-- 048 created the unique index uq_referral_tx_user_desc to make commission
-- credits idempotent by (internal_user_id, description). The migration runner
-- records a migration as applied only AFTER its SQL succeeds, so if duplicate
-- (internal_user_id, description) rows already existed when 048 first ran,
-- CREATE UNIQUE INDEX would error and the migration would stay un-applied,
-- retrying (and failing) on every subsequent deploy. This migration removes any
-- such duplicates first (keeping the physically-earliest row via ctid, so it does
-- not depend on a specific PK column) and re-asserts the unique index.
--
-- Safe where 048 already succeeded: the DELETE matches no rows and
-- CREATE UNIQUE INDEX IF NOT EXISTS is a no-op.
DELETE FROM referral_transactions
WHERE ctid IN (
    SELECT ctid
    FROM (
        SELECT ctid,
               ROW_NUMBER() OVER (
                   PARTITION BY internal_user_id, description
                   ORDER BY ctid
               ) AS rn
        FROM referral_transactions
    ) t
    WHERE rn > 1
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_referral_tx_user_desc
    ON referral_transactions (internal_user_id, description);
