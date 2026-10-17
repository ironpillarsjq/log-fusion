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

# Windows 分类视图（「融合日志管理」下的 Application / Security 子菜单）表格展示列。
# 完整字段（含 eventdata 原始 JSON）只在「原始详情」里展示，避免表格被大字段撑爆。
WINDOWS_VIEW_COLUMNS = ("time_created", "event_id", "level", "provider_name", "computer", "channel")

# 事件维度统计的图例说明。只收录有公开、稳定含义的编号；未收录的回退到
# 数据里该事件最常见的 provider_name（Application 通道的编号含义依赖提供程序），
# 两者都没有时前端只显示编号本身——不臆造解释。
WINDOWS_EVENT_LABELS = {
    # Security 通道常见审计事件
    "4624": "登录成功",
    "4625": "登录失败",
    "4627": "组成员身份信息",
    "4634": "会话注销",
    "4647": "用户发起注销",
    "4648": "显式凭据登录",
    "4672": "授予特殊权限",
    "4688": "创建进程",
    "4689": "进程退出",
    "4720": "创建用户账户",
    "4722": "启用用户账户",
    "4725": "禁用用户账户",
    "4726": "删除用户账户",
    "4728": "加入全局安全组",
    "4732": "加入本地安全组",
    "4756": "加入通用安全组",
    "4767": "解锁用户账户",
    "4771": "Kerberos 预认证失败",
    "4776": "凭据验证",
    "4907": "审核策略变更",
    "4946": "添加防火墙规则",
    "4947": "修改防火墙规则",
    "5058": "密钥文件操作",
    "5059": "密钥迁移操作",
    "5061": "加密操作",
    "5379": "读取凭据管理器",
    "1102": "清除审计日志",
    # Application 通道常见事件
    "1000": "应用程序错误",
    "1001": "错误报告",
}

# Linux 审计记录类型（analog：Windows 的 event_id）。event_id 在 Linux 侧是流水号，
# 同一张表几乎每行都不同，按它统计没有意义，所以事件维度用 type。
LINUX_TYPE_LABELS = {
    "SYSLOG": "系统日志",
    "USER_AUTH": "用户认证",
    "USER_ACCT": "账户操作",
    "USER_START": "会话开始",
    "USER_END": "会话结束",
    "USER_LOGIN": "用户登录",
    "USER_LOGOUT": "用户登出",
    "CRED_ACQ": "获取凭据",
    "CRED_DISP": "释放凭据",
    "CRED_REFR": "刷新凭据",
    "CRYPTO_KEY_USER": "密钥使用",
    "EXECVE": "执行命令",
    "PROCTITLE": "进程标题",
    "SYSCALL": "系统调用",
    "CWD": "工作目录",
    "PATH": "路径访问",
    "CONFIG_CHANGE": "配置变更",
    "SERVICE_START": "服务启动",
    "SERVICE_STOP": "服务停止",
    "LOGIN": "登录",
    "ADD_USER": "添加用户",
    "DEL_USER": "删除用户",
    "SOCKADDR": "套接字地址",
}

# 各表的真实列（2026-10-10 用 information_schema 只读核验，见 docs/DATABASE.md §4.3）。
# 入库前必须据此裁剪字段，否则会出现 1054 Unknown column。
LINUX_TABLE_FIELDS = {
    "authentication_session": ("event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id", "operation", "account", "executable", "hostname", "source_address", "terminal", "result", "tips"),
    "account_security_change": ("event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id", "operation", "executable", "hostname", "source_address", "terminal", "result", "tips"),
    "process_command_execution": ("event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id", "executable", "command", "result", "tips"),
    "file_object_access": ("event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id", "executable", "command", "object_path", "object_inode", "action", "result", "tips"),
    "network_ipc_communication": ("event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id", "executable", "command", "action", "source_address", "destination_address", "source_port", "destination_port", "protocol", "result", "tips"),
    "system_service_audit_lifecycle": ("event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id", "action", "target", "executable", "hostname", "result", "tips"),
    "security_policy_config_change": ("event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id", "action", "target", "executable", "result", "tips"),
}

# Windows 五张表列完全相同（同一份 DDL 建表）
WINDOWS_TABLE_FIELDS = (
    "source_year", "source_file", "provider_name", "provider_guid", "event_id",
    "event_version", "level", "task", "opcode", "keywords", "time_created",
    "event_record_id", "correlation_activity_id", "correlation_related_activity_id",
    "execution_process_id", "execution_thread_id", "channel", "computer",
    "security_user_id", "system_extra", "eventdata",
)
