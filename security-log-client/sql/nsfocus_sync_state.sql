-- MySQL 8.0+; additive migration, no existing data changed.
CREATE TABLE IF NOT EXISTS `log_fusion`.`nsfocus_sync_state` (
  `source_instance` VARCHAR(64) NOT NULL,
  `log_type` VARCHAR(64) NOT NULL,
  `state` JSON NOT NULL COMMENT 'Durable history/live queues, counters and retry state',
  `updated_at` DATETIME(3) NOT NULL,
  PRIMARY KEY (`source_instance`, `log_type`)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
