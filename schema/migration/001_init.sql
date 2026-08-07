-- schema/migration/001_init.sql
-- 可重复执行的初始化 SQL（MySQL 8.x 推荐）
-- 使用方式：
--   mysql -h$MYSQL_HOST -P$MYSQL_PORT -u$MYSQL_USER -p$MYSQL_PASSWORD < schema/migration/001_init.sql

CREATE DATABASE IF NOT EXISTS xhs_db DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE xhs_db;

CREATE TABLE IF NOT EXISTS generation_records (
    id                   INT AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id              VARCHAR(64)  NOT NULL UNIQUE COMMENT '对外暴露的 generation_id / 任务唯一 ID',
    status               VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending | success | failed',

    image_path           VARCHAR(512) NULL COMMENT '上传图片在本地/对象存储的路径',
    image_description    TEXT         NULL COMMENT '图片识图 / 描述结果（非空约束在应用层）',
    user_input           TEXT         NULL COMMENT '用户可选输入：风格偏好、关键词等',

    title                VARCHAR(100) NULL COMMENT '生成的标题（≤20字，应用层强制）',
    content              TEXT         NULL COMMENT '生成的正文（非空，应用层强制）',
    tags                 JSON         NULL COMMENT '标签数组，3~5 个，已去重并带 # 前缀',

    error_code           VARCHAR(64)  NULL COMMENT '失败时的错误码，见 ErrorCode',
    error_message        TEXT         NULL COMMENT '失败时的详细错误说明',

    created_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_task_id    (task_id),
    INDEX idx_status     (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='小红书文案生成任务记录';
