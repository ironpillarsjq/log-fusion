-- 原始日志存证库与存证表（log_hash）
--
-- 用途：每条采集到的日志在写入业务表（linux_logs / windows_logs）之前，先按
--       sha256(timestamp + 0x1F + log) 计算摘要，并把 log 原文与摘要一起保存，
--       用于事后防篡改校验。
--
-- 使用方式（在有权限的 MySQL 账号上执行一次）：
--     mysql -h <host> -P 3306 -u root -p < docs/sql/log_hash.sql
--
-- 注意：
--   1. 应用启动时会对本表执行 SQLAlchemy `create_all`，因此**不执行本脚本也能自动建表**；
--      本文件的作用是让表结构可复现、可审查（对应 docs/TODO.md「既有表没有仓库内正式 DDL」）。
--   2. `log_hash` 唯一索引：同一 (timestamp, log) 重复上报（Fluent Bit 重试）只留一条存证。
--      同一台机器在不同时刻产生的**相同内容**日志，因为 timestamp 参与哈希，摘要不同，
--      会各自成行——这是有意设计，保证存证与日志记录一一对应。
--   3. `raw_log` 用 LONGTEXT：Windows 单条事件（含 eventdata JSON）可能远超 TEXT 的 64 KB 上限。
--   4. 库名与表名都使用下划线而非连字符，避免每次引用都要写反引号。

CREATE DATABASE IF NOT EXISTS `log_hash`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `log_hash`.`log_hash` (
    `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '内部自增主键',
    `log_hash`      CHAR(64)     NOT NULL COMMENT 'sha256(timestamp + 0x1F + log) 的小写十六进制摘要',
    `raw_log`       LONGTEXT     NOT NULL COMMENT '原始日志正文，取自采集 JSON 的 log 字段',
    `log_timestamp` VARCHAR(64)  DEFAULT NULL COMMENT '参与哈希的原始时间字符串（不归一化）',
    `platform`      VARCHAR(16)  DEFAULT NULL COMMENT 'linux / windows',
    `source`        VARCHAR(255) DEFAULT NULL COMMENT '采集来源，如 ubuntu-audit / winlog',
    `client_id`     VARCHAR(128) DEFAULT NULL COMMENT '上报客户端标识',
    `event_time`    DATETIME     DEFAULT NULL COMMENT '解析后的 UTC 时间，仅用于查询排序，不参与哈希',
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '服务端入库时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_log_hash` (`log_hash`),
    KEY `ix_log_hash_client_id` (`client_id`),
    KEY `ix_log_hash_event_time` (`event_time`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '原始日志与 SHA-256 摘要存证';

-- 校验示例：确认某条存证的摘要与原文一致
--   SELECT id, client_id, event_time,
--          SHA2(CONCAT(log_timestamp, CHAR(31), raw_log), 256) = log_hash AS hash_ok
--   FROM `log_hash`.`log_hash`
--   ORDER BY id DESC
--   LIMIT 20;
--
-- 全表篡改巡检（找出原文被改动过的行）：
--   SELECT COUNT(*) FROM `log_hash`.`log_hash`
--   WHERE SHA2(CONCAT(log_timestamp, CHAR(31), raw_log), 256) <> log_hash;
