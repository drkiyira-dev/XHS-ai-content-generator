-- Run only after 004_generation_ownership.sql in the explicitly approved database.
-- Existing rows remain visible: all three new columns default to NULL.
-- Preview bytes live with MySQL history instead of the ephemeral upload tmpfs.

ALTER TABLE generation_records
    ADD COLUMN image_preview MEDIUMBLOB NULL AFTER image_path,
    ADD COLUMN image_preview_media_type
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER image_preview,
    ADD COLUMN deleted_at DATETIME(6) NULL AFTER updated_at,
    ADD CONSTRAINT ck_generation_records_image_preview CHECK (
        (image_preview IS NULL AND image_preview_media_type IS NULL)
        OR (
            image_preview IS NOT NULL
            AND image_preview_media_type IS NOT NULL
            AND image_preview_media_type IN ('image/webp', 'image/jpeg')
            AND OCTET_LENGTH(image_preview) BETWEEN 1 AND 262144
        )
    ),
    ADD CONSTRAINT ck_generation_records_deleted_after_created CHECK (
        deleted_at IS NULL OR deleted_at >= created_at
    );
