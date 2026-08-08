-- 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS xhs_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE xhs_db;

-- 创建 generation_records 表
CREATE TABLE IF NOT EXISTS generation_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(64) NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',

    image_path VARCHAR(512) NULL,
    image_description TEXT NULL,

    user_input TEXT NULL,

    title VARCHAR(100) NULL,
    content TEXT NULL,
    tags JSON NULL,

    error_code VARCHAR(64) NULL,
    error_message TEXT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_task_id (task_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
