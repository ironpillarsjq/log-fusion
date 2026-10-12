-- 存证归档台账表（log_fusion 库）
--
-- 作用：每次「导出并删除」存证后写一行，记录归档产物与范围。
-- 删除不可逆，本表是事后追溯「哪些记录在什么时候被导出到哪里」的唯一依据。
--
-- 应用启动时会通过 SQLAlchemy create_all() 自动创建本表；本文件用于
-- DBA 手动建库建表的可复现版本，以及在无应用环境下核对结构。
--
-- 执行方式（会创建 log_fusion 库，若已存在则跳过）：
--   mysql -h <host> -u <user> -p < docs/sql/archive_log.sql

CREATE DATABASE IF NOT EXISTS `log_fusion`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `log_fusion`.`archive_log` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `mode`          VARCHAR(16)  NOT NULL                COMMENT 'auto=自动归档 / manual=手动区间归档',
  `file_path`     VARCHAR(512)     NULL                COMMENT '归档 CSV 的绝对路径',
  `row_count`     BIGINT       NOT NULL DEFAULT 0      COMMENT '写入 CSV 的记录数',
  `deleted_count` BIGINT       NOT NULL DEFAULT 0      COMMENT '实际删除的行数，正常应等于 row_count',
  `file_sha256`   CHAR(64)         NULL                COMMENT 'CSV 文件内容 SHA-256，可校验文件未被改动',
  `range_start`   DATETIME         NULL                COMMENT '手动归档的开始时间（按 created_at）',
  `range_end`     DATETIME         NULL                COMMENT '手动归档的结束时间（按 created_at）',
  `before_time`   DATETIME         NULL                COMMENT '自动归档截止时间：created_at < before_time',
  `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '归档时间',
  PRIMARY KEY (`id`),
  KEY `ix_archive_log_mode` (`mode`),
  KEY `ix_archive_log_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='存证归档台账';

-- ---------------------------------------------------------------------------
-- 核验 SQL
-- ---------------------------------------------------------------------------

-- 1) 查看归档历史
-- SELECT id, mode, row_count, deleted_count, range_start, range_end, before_time,
--        file_path, created_at
-- FROM `log_fusion`.`archive_log`
-- ORDER BY id DESC LIMIT 20;

-- 2) 发现异常：导出行数与删除行数不一致（说明删除部分失败，需要人工核对）
-- SELECT id, mode, row_count, deleted_count, file_path, created_at
-- FROM `log_fusion`.`archive_log`
-- WHERE row_count <> deleted_count;

-- 3) 校验某个归档文件是否仍与台账记录一致（先在系统层算文件摘要）：
--    Windows:  Get-FileHash -Algorithm SHA256 <file_path>
--    Linux:    sha256sum <file_path>
--    再与下面的 file_sha256 对比：
-- SELECT id, file_path, file_sha256 FROM `log_fusion`.`archive_log` ORDER BY id DESC LIMIT 1;

-- 4) 当前待自动归档的存证条数（默认保留 3 个日历月，按 created_at）
-- SELECT COUNT(*) AS pending_archive
-- FROM `log_hash`.`log_hash`
-- WHERE `created_at` < DATE_SUB(CURRENT_DATE, INTERVAL 3 MONTH);
