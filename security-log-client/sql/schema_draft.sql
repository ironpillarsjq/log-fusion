-- HISTORICAL GENERIC DRAFT ONLY. NOT EXECUTED.
-- Current selected design: log_fusion_nsfocus_tables.sql (three dedicated tables in log_fusion).
-- Do not run this historical four-table draft as part of the selected three-table design.
-- Target: MySQL 8.0+, InnoDB, utf8mb4. Actual server version is unverified.
-- Run only against a separately selected database after review.
-- No CREATE DATABASE, USE, DROP, ALTER, user management or data modification.

CREATE TABLE security_fetch_batch (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_instance VARCHAR(64) NOT NULL COMMENT 'Stable configured product-instance identifier; no credentials',
    run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    data_kind VARCHAR(16) NOT NULL COMMENT 'log / event / related',
    page_no INT UNSIGNED NOT NULL,
    page_size INT UNSIGNED NOT NULL,
    request_json JSON NOT NULL COMMENT 'Query/body only; exclude passwords and Auth-Token',
    response_meta JSON NULL COMMENT 'Response envelope excluding data',
    total_records BIGINT UNSIGNED NULL,
    returned_records INT UNSIGNED NOT NULL DEFAULT 0,
    inserted_records INT UNSIGNED NOT NULL DEFAULT 0,
    duplicate_records INT UNSIGNED NOT NULL DEFAULT 0,
    rejected_records INT UNSIGNED NOT NULL DEFAULT 0,
    batch_status VARCHAR(24) NOT NULL COMMENT 'pending / committed / failed',
    response_file TEXT NULL COMMENT 'Local saved response for replay',
    response_file_sha256 BINARY(32) NULL COMMENT 'Digest of saved file bytes; not an authenticity signature',
    requested_at DATETIME(3) NOT NULL COMMENT 'UTC supplied by client',
    completed_at DATETIME(3) NULL COMMENT 'UTC supplied by client',
    error_message TEXT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_fetch_run_page (source_instance, run_id, data_kind, page_no),
    KEY ix_fetch_status_time (batch_status, requested_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE security_log (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_instance VARCHAR(64) NOT NULL,
    upstream_index VARCHAR(255) NOT NULL COMMENT 'API record.index; not source.table',
    upstream_log_id VARCHAR(255) NOT NULL COMMENT 'source.log_id',
    log_table VARCHAR(128) NULL COMMENT 'source.table',
    log_type BIGINT NULL COMMENT 'source.log_type',
    event_code BIGINT NULL COMMENT 'source.oevent_id; not source.eventidentifier',
    event_time DATETIME(3) NULL COMMENT 'UTC converted from source.timestamp seconds',
    received_time DATETIME(3) NULL COMMENT 'UTC converted from source.recv_time milliseconds',
    hostname VARCHAR(255) NULL COMMENT 'source.host, preserve original case/name',
    device_ip VARCHAR(64) NULL COMMENT 'source.dev_ip',
    source_ip VARCHAR(64) NULL COMMENT 'source.sip; genuinely optional',
    source_port INT NULL COMMENT 'source.sport; optional, no invented default 0',
    destination_ip VARCHAR(64) NULL COMMENT 'source.dip',
    operation VARCHAR(512) NULL,
    risk_value INT NULL COMMENT 'Raw value; mapping unverified',
    severity_value INT NULL COMMENT 'Raw value; mapping unverified',
    raw_record JSON NOT NULL COMMENT 'Complete API record including index and all source fields',
    record_sha256 BINARY(32) NOT NULL COMMENT 'Digest of defined canonical JSON; not raw transport bytes',
    first_batch_id BIGINT UNSIGNED NOT NULL,
    ingested_at DATETIME(3) NOT NULL COMMENT 'UTC supplied by client',
    PRIMARY KEY (id),
    UNIQUE KEY uq_log_upstream (source_instance, upstream_index, upstream_log_id),
    KEY ix_log_time (source_instance, event_time),
    KEY ix_log_code_time (source_instance, event_code, event_time),
    KEY ix_log_device_time (source_instance, device_ip, event_time),
    CONSTRAINT fk_log_batch FOREIGN KEY (first_batch_id) REFERENCES security_fetch_batch (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Provisional event projection from the manual example, NOT real event samples.
CREATE TABLE security_event (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    source_instance VARCHAR(64) NOT NULL,
    upstream_event_id VARCHAR(255) NOT NULL COMMENT 'eventNsuid',
    event_name VARCHAR(512) NULL COMMENT 'name',
    event_category VARCHAR(512) NULL COMMENT 'category1',
    event_time DATETIME(3) NULL COMMENT 'UTC converted from nstimestamp seconds',
    start_time DATETIME(3) NULL COMMENT 'UTC converted from startTime seconds',
    end_time DATETIME(3) NULL COMMENT 'UTC converted from endTime seconds',
    risk_value INT NULL COMMENT 'Manual says 1/3/5; preserve value until real data verified',
    alarm_state INT NULL COMMENT 'alarmState',
    action_value INT NULL COMMENT 'action',
    occurrence BIGINT NULL,
    rule_id VARCHAR(255) NULL COMMENT 'ruleId, string identifier',
    has_alarm TINYINT NULL COMMENT 'hasAlarm',
    valid_value TINYINT NULL COMMENT 'Preserve valid; its exact business meaning needs confirmation',
    destinations JSON NULL COMMENT 'destination is an array in the example',
    raw_record JSON NOT NULL COMMENT 'All original fields, including arrays and suggesstion typo',
    record_sha256 BINARY(32) NOT NULL,
    first_batch_id BIGINT UNSIGNED NOT NULL,
    last_batch_id BIGINT UNSIGNED NOT NULL,
    first_seen_at DATETIME(3) NOT NULL COMMENT 'UTC supplied by client',
    last_seen_at DATETIME(3) NOT NULL COMMENT 'UTC supplied by client',
    PRIMARY KEY (id),
    UNIQUE KEY uq_event_upstream (source_instance, upstream_event_id),
    KEY ix_event_time (source_instance, event_time),
    KEY ix_event_risk_time (source_instance, risk_value, event_time),
    CONSTRAINT fk_event_first_batch FOREIGN KEY (first_batch_id) REFERENCES security_fetch_batch (id),
    CONSTRAINT fk_event_last_batch FOREIGN KEY (last_batch_id) REFERENCES security_fetch_batch (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

-- Populate only after 3.5.5 returns a real event and the upstream log is resolved.
CREATE TABLE security_event_log (
    event_id BIGINT UNSIGNED NOT NULL,
    log_id BIGINT UNSIGNED NOT NULL,
    discovered_batch_id BIGINT UNSIGNED NOT NULL,
    linked_at DATETIME(3) NOT NULL COMMENT 'UTC supplied by client',
    PRIMARY KEY (event_id, log_id),
    KEY ix_event_log_log (log_id),
    CONSTRAINT fk_link_event FOREIGN KEY (event_id) REFERENCES security_event (id),
    CONSTRAINT fk_link_log FOREIGN KEY (log_id) REFERENCES security_log (id),
    CONSTRAINT fk_link_batch FOREIGN KEY (discovered_batch_id) REFERENCES security_fetch_batch (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
