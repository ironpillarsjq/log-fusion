LINUX_CATEGORIES = {
    "authentication_session": "认证会话",
    "account_security_change": "账号安全变更",
    "process_command_execution": "进程与命令执行",
    "file_object_access": "文件对象访问",
    "network_ipc_communication": "网络/IPC 通信",
    "system_service_audit_lifecycle": "系统服务/审计生命周期",
    "security_policy_config_change": "安全策略/配置变更",
}
WINDOWS_CATEGORIES = {
    "application": "Application",
    "security": "Security",
    "setup": "Setup",
    "system": "System",
    "forwardedevents": "ForwardedEvents",
}

LINUX_TABLE_COLUMNS = {
    "authentication_session": ["event_time", "event_id", "category", "type", "operation", "account", "executable", "hostname", "source_address", "result", "tips"],
    "account_security_change": ["event_time", "event_id", "category", "type", "operation", "executable", "hostname", "source_address", "result", "tips"],
    "process_command_execution": ["event_time", "event_id", "category", "type", "executable", "command", "result", "tips"],
    "file_object_access": ["event_time", "event_id", "category", "type", "executable", "command", "object_path", "action", "result", "tips"],
    "network_ipc_communication": ["event_time", "event_id", "category", "type", "executable", "command", "action", "source_address", "destination_address", "source_port", "destination_port", "protocol", "result", "tips"],
    "system_service_audit_lifecycle": ["event_time", "event_id", "category", "type", "action", "target", "executable", "hostname", "result", "tips"],
    "security_policy_config_change": ["event_time", "event_id", "category", "type", "action", "target", "executable", "result", "tips"],
}
