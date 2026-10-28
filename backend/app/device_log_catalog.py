"""WAF/DAS column truth and reviewable MySQL DDL; importing never connects or creates tables."""
from __future__ import annotations

import re


def fields(names: str, sql_type: str = "TEXT") -> dict[str, str]:
    return dict.fromkeys(names.split(), sql_type)


COMMON = {
    "ingest_id": "CHAR(36)", "source_instance": "VARCHAR(64)",
    "device_id": "VARCHAR(128)", "device_ip": "VARCHAR(45)",
    "device_hostname": "VARCHAR(255)", "peer_ip": "VARCHAR(45)",
    "transport": "VARCHAR(8)", "log_type": "VARCHAR(64)",
    "event_time": "DATETIME(6)", "event_time_raw": "TEXT",
    "received_at": "DATETIME(6)", "syslog_meta": "JSON",
    "raw_log": "LONGTEXT", "raw_bytes": "LONGBLOB", "raw_sha256": "BINARY(32)",
    "payload": "JSON", "parse_status": "VARCHAR(16)",
    "parse_error": "TEXT", "parser_version": "VARCHAR(32)",
}
WAF_TYPES = {
    **dict.fromkeys(("waf_log_login", "waf_log_op", "waf_log_system_run", "waf_log_wafstat"), "waf_audit_log"),
    **dict.fromkeys(("waf_log_l4acl", "waf_log_ddos", "waf_log_deface", "waf_log_webaccess",
                     "waf_log_arp", "waf_log_websec", "waf_log_ipblock", "waf_log_traffic_event",
                     "waf_log_session"), "waf_security_log"),
    "waf_log_site_traffic": "waf_traffic_log", "waf_log_bps_pps": "waf_traffic_log",
}
DAS_TYPES = {"audit": "das_statement_log", "session": "das_session_log", "warn": "das_sensitive_log"}
DAS_COMMON = {
    **fields("device_hash pid ip_type db_id", "VARCHAR(128)"),
    **fields("client_ip server_ip", "VARCHAR(45)"),
    **fields("client_port server_port", "SMALLINT UNSIGNED"),
    **fields("client_mac db_mac", "VARCHAR(64)"),
    **fields("db_name db_username os_username rule_id"),
    "risk_level": "VARCHAR(32)",
}
BUSINESS = {
    "waf_audit_log": {
        "src_ip": "VARCHAR(45)", "src_port": "SMALLINT UNSIGNED",
        **fields("user session_id desc op_type result role type source info"),
        **fields("cpu mem", "DECIMAL(5,2)"),
    },
    "waf_security_log": {
        **fields("event_type alertlevel protocol protocol_type http_protocol action block status", "VARCHAR(128)"),
        **fields("src_ip dst_ip raw_client_ip def_ip", "VARCHAR(45)"),
        "src_ips": "JSON",
        **fields("src_port dst_port proxy_port", "SMALLINT UNSIGNED"),
        **fields("site_id protect_id policy_id rule_id correlation_id tc_id", "VARCHAR(128)"),
        **fields("site_name vsite_name policy_desc attack_type block_info domain url uri method agent "
                 "wa_host wa_referer req_content_type res_content_type proxy_info country user_name "
                 "src_mac dst_mac def_mac conflit_mac reason api_event_type"),
        **fields("http alertinfo characters wci wsi", "LONGTEXT"),
        **fields("count_num req_content_len res_content_len", "BIGINT UNSIGNED"),
        **fields("waf_status_code ser_status_code", "SMALLINT UNSIGNED"),
        "is_api": "TINYINT UNSIGNED",
        **fields("rx_threshold_rate tx_threshold_rate rx_rate tx_rate", "DECIMAL(24,6)"),
    },
    "waf_traffic_log": {
        **fields("site_id group_id", "VARCHAR(128)"), "site_name": "TEXT",
        **fields("tps_rx tps_tx tps_drop req_bps pps_rx pps_tx", "DECIMAL(24,6)"),
    },
    "das_statement_log": {
        **DAS_COMMON,
        **fields("data_id ssid batch_id web_session_id sql_template_id", "VARCHAR(128)"),
        **fields("database_name rule_name req_time duration action_sub_type action1 action_result action_err_code "
                 "action_err_str rule_type alarm_mode tool_name db_host client_host db_ver table_names "
                 "is_sql batch_type web_client_ip web_username web_app_id ref_data_id zip_path attach_filename"),
        **fields("sql_statement mask_sql_statement resultset", "LONGTEXT"),
        **fields("duration_us ret_record_num", "BIGINT UNSIGNED"),
        "web_client_port": "SMALLINT UNSIGNED", "sensise_tag_array": "JSON",
    },
    "das_session_log": {
        **DAS_COMMON, **fields("data_id ssid", "VARCHAR(128)"),
        **fields("ss_action ss_result ss_err_code ss_err_str rule_type alarm_mode tool_name "
                 "db_host client_host zip_path ref_data_id"),
    },
    "das_sensitive_log": {
        **DAS_COMMON, "swarn_id": "VARCHAR(128)",
        **fields("table_names column_name client_tool sen_tag host_name web_client_ip web_username tpl_id sec_level"),
        "ret_record_num": "BIGINT UNSIGNED",
    },
}
TABLES = {name: {**COMMON, **columns} for name, columns in BUSINESS.items()}


def mysql_ddl(database: str = "log_fusion") -> str:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}", database):
        raise ValueError("invalid database name")
    statements = ["-- WAF/DAS Syslog additive migration. Apply explicitly; no runtime auto-DDL.",
                  "-- Select the existing database. Does not create or alter existing tables.",
                  f"USE `{database}`;"]
    required = {"ingest_id", "source_instance", "peer_ip", "transport", "log_type", "received_at",
                "raw_log", "raw_bytes", "raw_sha256", "payload", "parse_status", "parser_version"}
    for table, columns in TABLES.items():
        parts = ["  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY"]
        parts += [f"  `{name}` {kind} {'NOT NULL' if name in required else 'NULL'}" for name, kind in columns.items()]
        parts += ["  `ingested_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)",
                  "  UNIQUE KEY `uq_ingest_id` (`ingest_id`)",
                  "  KEY `idx_event_time` (`event_time`, `id`)",
                  "  KEY `idx_source_time` (`source_instance`, `event_time`, `id`)",
                  "  KEY `idx_type_time` (`log_type`, `event_time`, `id`)"]
        statements.append(f"CREATE TABLE IF NOT EXISTS `{table}` (\n" + ",\n".join(parts) +
                          "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;")
    return "\n\n".join(statements) + "\n"


if __name__ == "__main__":
    print(mysql_ddl(), end="")
