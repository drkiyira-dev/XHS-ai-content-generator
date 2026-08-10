-- Run only after selecting the explicitly approved MySQL database.
-- This migration never creates, selects, or drops a database.

CREATE TABLE generation_records (
    id INTEGER NOT NULL AUTO_INCREMENT,
    task_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    image_path VARCHAR(512) NULL,
    image_description TEXT NULL,
    user_input TEXT NULL,
    title VARCHAR(100) NULL,
    content TEXT NULL,
    tags JSON NULL,
    error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    error_message TEXT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_generation_records_task_id UNIQUE (task_id),
    CONSTRAINT ck_generation_records_status
        CHECK (status IN ('pending', 'success', 'failed')),
    INDEX ix_generation_records_status (status),
    INDEX ix_generation_records_created_at (created_at)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;
