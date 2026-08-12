-- Run only after selecting the explicitly approved MySQL database.
-- This migration adds local demonstration accounts and revocable sessions.
-- It never creates, selects, alters, or drops a database.

CREATE TABLE users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    email VARCHAR(254) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin NOT NULL,
    password_hash VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    email_verified_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    last_login_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_email_normalized CHECK (
        LENGTH(email) BETWEEN 3 AND 254
        AND email = TRIM(email)
        AND email = LOWER(email)
    ),
    CONSTRAINT ck_users_is_active CHECK (is_active IN (0, 1)),
    CONSTRAINT ck_users_password_hash_not_empty CHECK (
        LENGTH(password_hash) > 0
    ),
    CONSTRAINT ck_users_updated_after_created CHECK (updated_at >= created_at),
    CONSTRAINT ck_users_verified_after_created CHECK (
        email_verified_at IS NULL OR email_verified_at >= created_at
    ),
    CONSTRAINT ck_users_login_after_created CHECK (
        last_login_at IS NULL OR last_login_at >= created_at
    )
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE auth_sessions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    token_hash VARBINARY(32) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    last_seen_at DATETIME(6) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    revoked_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_auth_sessions_token_hash UNIQUE (token_hash),
    CONSTRAINT fk_auth_sessions_user_id FOREIGN KEY (user_id)
        REFERENCES users (id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,
    CONSTRAINT ck_auth_sessions_token_hash_length CHECK (
        OCTET_LENGTH(token_hash) = 32
    ),
    CONSTRAINT ck_auth_sessions_expires_after_created CHECK (
        expires_at > created_at
    ),
    CONSTRAINT ck_auth_sessions_last_seen_range CHECK (
        last_seen_at >= created_at AND last_seen_at <= expires_at
    ),
    CONSTRAINT ck_auth_sessions_revoked_after_created CHECK (
        revoked_at IS NULL OR revoked_at >= created_at
    ),
    INDEX ix_auth_sessions_user_state_expiry (
        user_id,
        revoked_at,
        expires_at
    ),
    INDEX ix_auth_sessions_expires_at (expires_at)
) ENGINE=InnoDB
  DEFAULT CHARACTER SET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;
