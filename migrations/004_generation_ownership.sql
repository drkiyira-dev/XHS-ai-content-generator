-- Run only after 003_auth_tables.sql in the explicitly approved database.
-- This first ownership phase preserves every existing row with user_id NULL.
-- Backfill and NOT NULL enforcement require a separate reviewed migration.

ALTER TABLE generation_records
    ADD COLUMN user_id BIGINT UNSIGNED NULL AFTER id,
    ADD INDEX ix_generation_records_user_status_created (
        user_id,
        status,
        created_at
    ),
    ADD CONSTRAINT fk_generation_records_user_id FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT;
