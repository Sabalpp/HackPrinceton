-- Apply during a stopped-worker maintenance window to a pre-context preview DB.
-- Fresh databases get these columns from runtime_models via init_db.
-- Existing pending messages remain unscoped and will cancel, never infer consent.
BEGIN;
ALTER TABLE delivery_jobs
    ADD COLUMN IF NOT EXISTS parent_id VARCHAR,
    ADD COLUMN IF NOT EXISTS visibility VARCHAR NOT NULL DEFAULT 'unscoped',
    ADD COLUMN IF NOT EXISTS recipient_role VARCHAR,
    ADD COLUMN IF NOT EXISTS account_required BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS account_created_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS family_link_id INTEGER,
    ADD COLUMN IF NOT EXISTS care_circle_id INTEGER,
    ADD COLUMN IF NOT EXISTS request_id VARCHAR,
    ADD COLUMN IF NOT EXISTS offer_only BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS require_share_health BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS require_share_checkins BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS require_emergency BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS ix_delivery_jobs_parent_id ON delivery_jobs(parent_id);
COMMIT;
