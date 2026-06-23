-- Drop the LTE-fleet vpn_servers rows.
-- The 4 whitelisted LTE entries (id 10/12/13/14) are decommissioned: their RU
-- servers are offline and the LTE product is removed. No FK references vpn_servers
-- (verified), so a hard DELETE is safe. Idempotent.
DELETE FROM vpn_servers WHERE id IN (10, 12, 13, 14);
