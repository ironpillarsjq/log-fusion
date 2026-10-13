# 变更日志

本文件记录当前 FastAPI 实现的可追踪变化，格式参考 Keep a Changelog。由于现有 Git 历史和旧文档不足以可靠还原所有版本，暂不编造历史版本号。

## Unreleased

### Docs — 2026-10-13（文档与真实代码/数据库逐条核对后的修正）

- 触发：全面复核 `docs/`、`AGENTS.md`、`backend/README.md` 与代码及**四个真实库**的一致性。
  本次数据库访问**全部只读**（`information_schema` + `SELECT`），未执行任何 DDL/DML。发现并修正：
  - `docs/API.md` §1 称「只有一个中间件，对所有 `/static/*` 加 `no-cache`」，与 `_cache_policy` 的三类策略
    （`/static/vendor/*` → `max-age=31536000, immutable`、其他 `/static/*` → `no-store`、HTML → `no-store`）不符。
  - `docs/sql/archive_log.sql` 与 `docs/DATABASE.md` §3.3 写 `file_sha256 CHAR(64)` 并多带一个
    `ix_archive_log_created_at`；**真实表是 `varchar(64)`、索引只有 `ix_archive_log_mode` + 主键**
    （即 `create_all()` 的形态）→ DDL 与文档按真实表改正。
  - `docs/DATABASE.md` §3.2 补注：真实 `log_hash.log_hash` 是 `char(64)` + `uq_log_hash`（与
    `docs/sql/log_hash.sql` 一致），而单靠 `create_all()` 会建出 `varchar(64)` + `ix_log_hash_log_hash`；
    建表应以 SQL 脚本为准，两种形态已在文档中写明。
  - `log_fusion` 库实际有 **3 张表**，其中一张是 **0 行的遗留空表 `log_hash`**（`LOG_HASH_DB` 拆分前由
    `create_all()` 建在此处）：补入 `DATABASE.md` §1/§10、`AGENTS.md`，并在 `TODO.md` 低优先级登记
    （删除属数据库变更，需单独授权，本次未动）。
  - `docs/README.md` §8 的 `/health` 示例只有 3 个库 → 补 `log_hash`。
  - `docs/API.md` §7.4 与接口总表把 `/api/v1/archive/preview` 描述成「待归档条数」，实际**不带参数时
    `candidates` = 全表条数**（走的是「手动归档空区间」语义，不是自动归档候选集）→ 补充说明，
    并注明 `/archive/manual` 会拒绝空区间、`/archive/auto` 只按 `created_at < months_ago(3)` 选取
    （实测：34099 行、3 个日历月窗口下自动归档候选为 **0**；前端无参调用仅用于读取 `retentionMonths`）。
    这是**文档描述问题，不是数据风险**：两条删除路径都有守卫。
  - `docs/DECISIONS.md` ADR-007 仍写「临时采用：读 `linux_logs.audit_log_evidence`」→ 标记
    **已被取代（2026-10-11）**，原文保留作历史，并注明其「后续」清单已完成。
  - `docs/TODO.md`：「遗留项见下方『存证逐条校验接口』」是**悬空引用**（全文无此节）→ 补上真正的条目；
    「三个库 / 四个库」口径统一为四个；新增 `log_fusion.log_hash` 遗留空表清理项。
  - `README.md` §4/§6.1、`backend/README.md`、`AGENTS.md` 的「三个逻辑库」「全部走 CDN」
    「存证读取兼容层（表缺失返回空集合）」等表述 → 更新为四库、自托管、`log_hash` 真实读取 +
    503 语义 + `errors[].stage` 含 `hash`；§4 技术栈表补 `alembic` 说明（依赖已引入、无迁移脚本）；
    §6.1 测试说明更新为 **166 用例**并列出新增覆盖（存证哈希、503 语义、归档不变量、文档一致性）。
- 本次核对结论（可作为后续基线）：
  - `linux_logs` 7 表、`windows_logs` 5 表与 `catalog.py` 的类别映射**一一对应**；
    `backend/app/main.py` 实际 **23 条路由**与 `docs/API.md` §2 总表逐条一致（无幽灵接口、无漏记）。
  - **存证链自校验通过**：对全部 **34099** 行执行
    `SHA2(CONCAT(log_timestamp, CHAR(31), raw_log), 256) <> log_hash`，**不一致 0 行**。
  - `log_fusion.archive_log` 有 2 条台账（34099 行、35 行，均为 manual），其中一条 `file_path`
    是相对路径、另一条是 `D:\log-fusion\...`，对应「归档目录锚定仓库根」修复前后的两次运行。
- 本次**无代码变更**、无接口行为变化；`docs/` 一致性由 `tests/test_doc_sync.py` 继续守护。

### Fixed — 2026-10-12（存证故障被伪装成「0 条」与「已存证」）

排查「存证页显示 0 条、但数据库里明明有数据」时发现的两个缺陷，都属于
「故障被伪装成正常」，正是本项目文档反复强调要避免的模式：

- **`/api/v1/evidence/summary`、`/api/v1/evidence`、`/api/v1/evidence/export`
  把数据库异常吞掉并返回 `total: 0` / 空列表 / 空 CSV。** 结果是「存证库连不上」
  和「确实没有存证」在页面上完全一样，操作者会误以为数据被归档清空了。
  现在改为记录日志并返回 **503**，前端会弹出错误提示而不是显示一个自信的 `0`。
- **`_persist_evidence` 把所有 `IntegrityError` 都当成「重复上报」计为存证成功。**
  MySQL 下 NOT NULL 违反等真实错误同样抛 `IntegrityError`，只有 errno **1062**
  才代表唯一键冲突。现在新增 `_is_duplicate_key()` 按 errno 判定（驱动未提供
  errno 时按 `Duplicate entry` 文本兜底），非重复的完整性错误如实计入
  `errors[].stage="hash"`，不再让存证静默丢失却报 `hashed` 正常。
- 新增 **`tools/restore_archive.py`**：把归档 CSV 导回 `log_hash` 表（默认只预览，
  `--apply` 才写库；靠唯一索引幂等跳过已有记录，`created_at` 按 CSV 原值写入，
  这样回灌后按入库时间的归档判断与原始状态一致）。批量提交（1000 行/批），
  实测 34099 行约 40 秒；重复执行同一文件新增 0 行。归档是「导出后删除」，
  此工具用于归档误删后的恢复或继续测试。
  - 计数用**表行数差值**而非 `rowcount`：驱动可能开启 `CLIENT_FOUND_ROWS`，
    那样「命中已有行」也会被算成 1，导致把跳过误报成新增。
- 文档：`docs/API.md` §7.1/§7.2/§7.3 明确 503 语义并记录 `evidence/export`
  不含 `raw_log`；`docs/README.md` 补恢复工具用法；`docs/DATABASE.md` §7 补异常行为行。
- 测试：新增 6 个用例（503 语义、errno 1062 判定、非重复完整性错误必须计入 failed，
  含 1048/1406 与文本兜底）。测试总数 159 → 166。

### Added — 2026-10-11（存证归档导出 + 删除、存证页改用 log_hash、列表分页）

- **存证归档（导出并删除）**，新增 `backend/app/archive.py`：
  - **自动归档**：导出 `created_at` 早于 `archive_retention_months`（默认 3）个**日历月**的全部存证，
    再从数据库删除。按 `created_at`（入库时间）而非 `event_time` 判断——否则客户端补传的
    历史日志会刚入库就被归档掉。后台任务每天执行一次，启动后延迟 60 秒跑第一次。
  - **手动归档**：选择时间区间（`start`/`end`，`YYYY-MM-DD`）导出后删除。
  - CSV 均为 UTF-8 带 BOM（Excel 直接可读），列为
    `id, log_hash, log_timestamp, platform, source, client_id, event_time, created_at, raw_log`，
    **含 log 原文**；文件永久保留在 `archive_dir`（默认 `data/archive`），不做自动清理。
  - **安全流程**：选 id → 分批取行 → 流式写 CSV → `flush`+`fsync` → 校验文件存在、大小 > 0、
    行数与 id 数一致 → **才**执行 `DELETE`。任一步失败立即中止，**一条都不删**。
  - 用**明确的 id 列表**删除，而不是 `DELETE WHERE created_at < X`：否则归档期间新写入、
    同样满足条件的记录会被误删。
  - 新增归档台账表 `log_fusion.archive_log`：记录 `mode`/`file_path`/`row_count`/`deleted_count`/
    `file_sha256`/时间区间。删除不可逆，台账是事后追溯的唯一依据；台账写入失败不影响已完成的归档。
- **存证页改用真实的 `log_hash` 表**（原先读的 `linux_logs.audit_log_evidence` 在真实库中不存在，
  页面永远是空的）：`/api/v1/evidence` 三个接口全部改读 `log_hash`，`_evidence_rows()` 删除，
  `status` 过滤参数与 `verify_status`/`evidence_id`/`raw_hash` 等模板字段移除。
  分页改为数据库侧 `LIMIT/OFFSET`（原先一次性取回再在 Python 切片，且只覆盖最近 1000 条）。
- **列表分页**：融合日志与存证列表均为一页 **30 条**，带上一页/下一页与「共 N 条，第 X / Y 页」。
  融合日志之前 `page` 写死为 1（后端本来就支持分页，只是没用），切换平台/类别/关键词时会回到第 1 页。
- **归档目录锚定仓库根**：`archive_dir` 默认是相对路径，而相对路径按进程工作目录解析——
  从 `backend/` 启动和从仓库根启动会写到不同位置，台账里记的路径也随之变化。
  现在统一按仓库根展开为绝对路径（实测修复前文件落在 `backend/data/archive`）。
- 新增 `tests/test_archive.py`（21 个用例）锁住删除场景的关键不变量：落盘失败不删、
  行数不一致中止、0 字节文件中止、CSV 记录集合等于被删 id 集合、空结果不建文件、
  按 id 删除、日历月回退（含 3-31 → 2-28 收敛）、台账失败被吞掉、CSV 逗号/引号转义。测试总数 138 → 159。
- `config.py` 新增 `ARCHIVE_DIR`、`ARCHIVE_RETENTION_MONTHS`、`ARCHIVE_BATCH_LIMIT`；
  `models.py` 新增 `ArchiveLog`。
- 接口新增 4 条：`GET /api/v1/archive/preview`、`POST /api/v1/archive/manual`、
  `POST /api/v1/archive/auto`、`GET /api/v1/archive/log`；接口总数 19 → 23。
- 同步文档：`docs/API.md` §2/§7（新增 §7.4～§7.7 与归档安全流程）、
  `docs/DATABASE.md` §1/§3.3/§6/§9/§10、`docs/ARCHITECTURE.md` §1/§2、`docs/README.md`。
- **数据库变化**：新增表 `log_fusion.archive_log`（启动时自动创建）；`log_hash.log_hash` 结构未变。
- **接口变化**：`/api/v1/evidence` 增 `platform`/`keyword` 参数、`size` 默认 20→30、响应增 `pages`、
  移除 `status`；`/api/v1/evidence/export` 增筛选参数、移除 `status`。
- 实测（2026-10-12，真实库）：`log_hash` 表 34099 条整日归档导出 13,746,826 字节 CSV，
  CSV 解析器读回 34099 条、`raw_log` 无空值、`log_hash` 全 64 位，数据库剩余 0 条，
  台账 `rows=34099 deleted=34099`，文件 SHA-256 与接口返回值一致。
  分页实测：存证 35 条 → 第 1 页 30 条 / 第 2 页 5 条，两页无重叠且覆盖全部。

### Added — 2026-10-11（原始日志 SHA-256 存证入库）

- 新增第四个逻辑库 `log_hash` 与存证表 `log_hash.log_hash`：每条采集日志在写入业务表之前，
  按 `sha256(timestamp + 0x1F + log)` 计算摘要，并把 `log` 原文与摘要一起保存，用于事后防篡改校验。
  - 哈希输入固定为**采集端发来的原始字符串**（不 trim、不归一化、不转义），分隔符用 US（`0x1F`）
    避免 `timestamp+log` 直接拼接产生歧义；`log` 中的控制字符（如 auditd 的 `0x1D`）按原始字节参与哈希。
  - **`timestamp` 参与哈希**：syslog/auditd 中同一台机器在不同时刻产生内容完全相同的行很常见；
    若只哈希 `log`，这些行会得到同一摘要并被唯一索引吞掉，存证将无法与日志一一对应。
  - `log_hash` 列有唯一索引：同一 `(timestamp, log)` 重复上报（Fluent Bit 重试）只留一条存证，
    唯一键命中按幂等成功处理。
  - 落库失败/缺字段的降级：记录缺少 `log` 或内容为空（含全空白）时**跳过存证**，
    在响应 `errors[]` 追加 `stage="hash"`，但**不影响日志入库**；`persisted` 只反映业务表结果。
  - 新增可复现 DDL `docs/sql/log_hash.sql`（含 MySQL 侧复算摘要的校验 SQL）。
- 代码新增 `backend/app/hashing.py`（纯函数：摘要计算 + 存证行构造 + 复算校验），与 `parsers.py` 同为可单测的解析层。
- `config.py` 新增 `LOG_HASH_DB`（默认 `log_hash`）与 `LOG_HASH_TABLE`（默认 `log_hash`）；
  `db.py` 新增 `hash_engine`；`models.py` 新增 `LogHash` ORM 模型；
  启动时对 `hash_engine` 执行 `create_all(tables=[LogHash.__table__])`，失败只记日志不中断启动。
- `POST /api/v1/logs/{platform}` 响应新增 `hashed` 字段，`errors[].stage` 新增取值 `hash`。
- `/health` 与 `/partials/db-status` 新增 `log_hash` 连通性。
- 新增 `tests/test_hashing.py`（18 个用例）锁住哈希输入定义：真实 auditd 样例、
  同内容不同时间戳摘要不同、分隔符防拼接碰撞、首尾空白与控制字符按原文参与哈希、
  缺 `log` 跳过存证、`raw_log` 不被列宽裁剪。测试总数 120 → 138。
- 同步文档：`docs/DATABASE.md` §1/§2/§3.2、`docs/API.md` §2/§3.2/§3.3/§5.1、
  `docs/ARCHITECTURE.md` §1/§2/§3/§9/§10/§12、`docs/README.md` §1/§2/§3/§4/§5。
- **数据库变化**：新增库 `log_hash` 与表 `log_hash`（执行 `docs/sql/log_hash.sql` 或由应用启动自动建表）；
  既有 `linux_logs`/`windows_logs`/`log_fusion` 的表结构未变。
- **接口变化**：`/api/v1/logs/{platform}` 响应体新增 `hashed`；其余接口路径与参数不变。

### Docs — 2026-10-10（澄清心跳与日志总量的口径）

- 疑问来源：「收到心跳后，心跳数据会不会当成日志入库、算不算在日志总量里」。答案是两条独立链路，代码与线上数据均已核验：
  心跳经 `persist_heartbeats()` 只 upsert 到 `log_fusion.client_status`，而 `/api/v1/logs/*`（汇总、列表、搜索、导出、
  `/recent`）只读 `linux_logs`/`windows_logs`；心跳走 WebSocket `/ws/client-monitor` 广播，不进入日志 SSE 队列。
- `docs/API.md` §4.2 补充：`/api/v1/logs/summary` 的 `total` 只含 12 张日志表，心跳客户端数见 §6 `/api/v1/monitor/summary`。
- `docs/DATABASE.md` §3.1 补充：`client_status` 不是日志表、不计入日志总量（附实测：6 行 / 6 个客户端，
  同一时刻 `total` 为 425812 = Linux 160552 + Windows 265260）；并记录 `INSERT ... ON DUPLICATE KEY UPDATE`
  也会消耗自增值，因此 `AUTO_INCREMENT=1857` 不等于 1857 行心跳。

### Fixed — 2026-10-10（概览环形图图例压住图形）

- 概览「日志类别分布」原本只有一个 280px 高的容器：12 个类别全塞在 `legend:{bottom:0}`，
  外侧标签还带完整类别名，结果图例折成多行文字糊成一团、与环形图相互压住（见用户截图）。
  现在按宽度分两种布局：
  - 宽屏（容器 ≥ 560px）：图例改为右侧竖排一列（`orient:'vertical'`，`textStyle.width` 截断 + 悬停看全名），
    环形图左移到 `center:['32%','50%']`，两者分列互不遮挡（827px 宽时水平净距约 246px）。
  - 窄屏（< 560px）：图例移到下方并允许折行（`type:'plain'`——`scroll` 型在窄屏会分页成「1/3」并把条目顶出画布），
    环形图上移缩小到 `center:['50%','38%']`、`radius:['30%','50%']`，并关闭外侧标签。
- 标签只显示百分比（`{d}%`），`minShowLabelAngle:5` 让占比过小的切片不再画标签（原来长类别名本身就是重叠源）；
  完整名称与条数保留在 tooltip 里。
- **0 条类别不再进入环形图**：它们只会往图例里堆字，图下新增一行说明「另有 N 个类别当前为 0 条」。
- 顺带修掉 `drawChart` 的两个隐藏缺陷：① 每次刷新都 `echarts.init(el)` 新建实例（既泄漏又在控制台刷
  "instance already initialized" 告警），现在复用同一实例；② `x-show` 隐藏区块时容器宽高为 0，
  原实现会把图画成 0×0，现在检测到不可见直接跳过，并在切回概览时补画（`showOverview()`）。窗口 `resize`
  也会重画（150ms 防抖），否则图形与图例错位。
- 容器高度 280px → 300px，`app.js` 版本参数 → `?v=20261010-2`（强制刷新旧缓存）。
- 新增 `tools/chart-layout-check.cjs`：用真实 `app.js` 的 `drawChart` + 真实 `frontend/vendor` 的 ECharts
  服务端渲染（SVG）在 420/640/827/1200 四种宽度上断言——图例文本包围盒与「弧线 + 引导线 + 标签」包围盒不相交、
  内容不被画布裁掉、弧线圆心/半径与 option 声明一致、0 值类别已剔除、容器不可见时不产出 option。
  本机沙箱无法运行无头浏览器（Edge 无头模式不渲染、CDP 端口不监听），这是当前唯一可复现的图表布局验证手段。

### Fixed — 2026-10-10（采集解析与入库，解决“不知道哪一步失败”）

- 新增 `backend/app/parsers.py`：把采集记录解析成可直接入库的行，逻辑参考只读的
  `旧后端/log-backend/src/main/python/{linux,windows}`（`process_logs.py`、`audit_to_mysql_csv.py`、`evtx_parser.py`、`schema.py`）。
- **修复采集一直静默失败**：此前时间字段以原始字符串写入 MySQL `datetime` 列，必然报
  `(1292, "Incorrect datetime value")` —— Linux 是 `2026-10-02T09:31:59.929001Z`，
  Windows 是 `2026-10-02 20:03:18 +0800`。现在统一用 `parse_datetime()` 解析成 UTC `datetime`
  （支持 ISO 带 `Z`/偏移、`%Y-%m-%d %H:%M:%S %z`、epoch 秒/毫秒/微秒、无年份 syslog 行首）。
- Linux 解析补齐：auditd 行按类型映射到 7 张表（`SYSCALL` 依 `syscall` 判定进程/文件类，
  `EXECVE`/`PROCTITLE`→进程与命令，`SOCKADDR`/`NETFILTER_*`→网络/IPC，`USER_AUTH`/`CRED_*`→认证会话），
  `EXECVE` 参数拼成 command，operation/action/result 归一化，未收录类型按解析失败上报；
  非 auditd 的 syslog 行归入 `authentication_session`（`type=SYSLOG`），并抽取 account
  （兼容 pam 的 `for user X` 与 sshd 的 `for X from`）。
- Windows 解析补齐完整列映射：`provider_guid`/`level`/`task`/`opcode`/`keywords`/`correlation_*`/
  `execution_*`/`security_user_id`，`eventdata` 汇总 `Data`/`Message`/`EventType` 等，其余键进入
  `system_extra`，两者写 JSON 字符串；`source_year` 取事件年份。
- 行字段按目标表真实列裁剪（`catalog.LINUX_TABLE_FIELDS` / `WINDOWS_TABLE_FIELDS`）：整型列空值写
  `NULL`（避免 1366）、字符串按列宽截断（避免 1406）、缺列字段并入 `tips`（避免 1054 且不丢信息）。
- `POST /api/v1/logs/{platform}` 响应新增 `inserted` / `skipped` / `failed` / `errors`：
  `errors[].stage` 为 `parse` 或 `insert`，`index` 指向请求数组下标；整批写入失败会退化为逐行重试以定位记录。
  `persisted` 现在等于“全部记录都成功写入”。原先的 `print` 改为 `logger`。
- `event_id` 的兜底生成改用 sha256 稳定值（原实现用 Python `hash()`，跨进程会变），`TODO.md` 对应技术债已移除。
- 新增 `tests/test_parsers.py`（解析层纯函数用例）与采集接口的新契约用例；测试总数 118，全部不连接数据库。

### Verified — 2026-10-10（真实样例端到端）

- 用仓库外的真实样例（`data/linux/raw-logs.log`、`data/windows/raw-logs.log`）走 HTTP：
  `POST /api/v1/logs/linux`、`/windows` 均返回 `{"persisted": true, "inserted": 1, ...}`，数据库行数同步 +1；
  Linux 入库行 `type=SYSLOG`、`account=root`、`hostname=wlh-virtual-machine`，Windows 入库行
  `channel=Security`、`computer=Pillar-Desktop`、`time_created=2026-10-02 12:07:16`（UTC，原始为 `20:03:18 +0800`）。
- 负向用例：没有时间信息的记录返回 `persisted:false, skipped:1, errors[0].stage="parse"`；
  混合请求返回 `inserted:1, skipped:1` 并给出失败下标。

### Changed — 2026-10-10（前端实时更新改为 WebSocket + 定时刷新）

- 前端不再订阅 `/api/v1/logs/stream`（移除 `EventSource`），只保留 1 条心跳 WebSocket 长连接（ADR-012）。
  - 原因：浏览器对同一地址的 HTTP/1.1 连接上限是 6 条；前端每页原本占用 1 条 SSE + 1 条 WebSocket，刷新与多标签会让 SSE 累积（实测 `/health` 的 `sse_clients` 达到 6），占满额度后**所有 REST 请求（含自动刷新）永久排队**——表现为页面刷新一直转圈、心跳页无数据、只能手动刷新，而服务端接口仍是 13–21 ms。
- 自动更新节奏：心跳页由 WebSocket 快照驱动（每 5 秒广播一次）；概览统计每 10 秒刷新；日志列表与存证页在打开时每 15 秒刷新；WebSocket 断开时退化为 5 秒轮询 REST。概览的「实时通道」指标改为反映真实连接状态（正常 / 降级轮询）。
- `dashboard.html` 的 `app.js` 引用加版本参数（`/static/app.js?v=20261010-1`）：旧浏览器缓存过没有 `no-store` 头的副本，需要一次强制定位；此后该文件始终带 `no-store`。
- SSE 接口保留给外部消费方，行为与文档不变；`/health` 的 `realtime.sse_clients`/`ws_clients` 继续可用于排查连接占用。

### Fixed — 2026-10-10（页面依赖自检与缓存）

- HTML 响应改为 `Cache-Control: no-store, must-revalidate`：此前页面无缓存头，浏览器可能继续使用**引用了 CDN 依赖的旧页面**；旧页面在 CDN 不可达时 Alpine 无法初始化，所有带 `x-cloak` 的区块全部隐藏、导航点击无反应，表现成“界面不出来、没反应”。
- 模板新增依赖自检：`load` 后 1.5 秒检查 `window.Alpine`/`window.htmx`/`window.echarts`，缺失则在顶部显示红色提示条（含 Ctrl+F5 提示与 `/static/vendor/` 排查指引），不再留下白页。
- 中间件更名为 `_cache_policy`，按 `/static/vendor/*`（长缓存）、其他 `/static/*`（禁缓存）、HTML 页面（禁缓存）三类设置响应头。

### Fixed — 2026-10-10（启动入口"闪退"）

- 新增 `启动服务.cmd`、`启动模拟请求.cmd` 与 `tools\run-server.ps1`、`tools\run-simulator.ps1` 转发脚本。
  - 根因：本机 `.ps1` 的默认文件关联是 **Windows 记事本**（`AppX…Microsoft.WindowsNotepad`，`PackageRelativeExecutable = Notepad\Notepad.exe`），双击 `.ps1` 不会执行脚本，只是让记事本打开它（表现为窗口一闪即退）。
  - `.cmd` 内容保持纯 ASCII，中文文件名只出现在文件名本身，避免 cmd.exe 按控制台代码页（本机 GBK）解析批处理内容时把中文路径读成乱码；转发脚本用 UTF-8 with BOM 保存，可安全写中文路径。
- `启动模拟请求.ps1` 增加全程 transcript 日志（`%TEMP%\log-fusion-simulator.log`）与启动上下文（PowerShell 版本、宿主、CWD、脚本路径、参数），错误分支额外打印出错位置行；`Ctrl+C`（退出码 `0xC000013A`）按正常停止处理。

### Changed — 2026-10-10（前端依赖本地化）

- 前端依赖由 jsDelivr CDN 改为自托管：Bootstrap 5.3.3、HTMX 2.0.4、Alpine.js 3.14.8、ECharts 5.5.1 存放于 `frontend/vendor/`（文件名内嵌版本号），`dashboard.html` 只引用 `/static/vendor/...`。
  - 触发原因：本机 DNS 被代理接管，`cdn.jsdelivr.net` 解析为 `198.18.0.x` 的 fake-IP，四个库的请求需绕经代理 TUN，首屏多出数秒等待。实测四个库本地加载合计约 100 ms，经代理约 2 s。
  - 决策记录见 ADR-011；升级流程与许可证见 `frontend/vendor/README.md`。
- 静态资源缓存策略调整：`/static/vendor/*` 改为 `public, max-age=31536000, immutable`（文件名带版本号），`app.js` 等应用自身资源继续 `no-cache, no-store`（原中间件 `_no_cache_static` 更名为 `_static_cache_policy`）。
- 模拟器 `send_simulated_clients.py` 改用 `httpx.Client(trust_env=False)`：忽略 `HTTP_PROXY`/`HTTPS_PROXY` 等环境代理，避免发往 `127.0.0.1` 的请求被代理拦截（实测曾返回 502）。
- `启动模拟请求.ps1` 加固：新增 `-NoPause`；报错时打印排查建议并等待按键（双击运行时不再“闪退”看不到原因）；`Ctrl+C` 结束（退出码 `0xC000013A`）按正常停止处理而不再抛错。

### Tests — 2026-10-10

- 新增 `tests/` 与 `pytest.ini`，仓库根目录 `python -m pytest` 可重复运行，当前 72 个用例通过。
- `tests/conftest.py` 在导入 `app.*` 之前把连接参数改写为不可用占位值，并用类级 patch 禁用 `sqlalchemy.engine.Engine.connect/begin`：任何漏改的数据库访问都会直接失败，因此整套测试**不连接任何数据库**。
- 覆盖范围：类别名 ASCII 校验、列表/导出取数上限、关键词与 `/recent` 的按表列适配、`recent_rows` 合并排序与类型混用、采集降级与非对象载荷、SSE 摘要字段来源、心跳状态阈值与 503 降级、查询参数边界、静态资源缓存策略、**模板不得再引用公网 CDN**，以及路由集合与 `docs/API.md` §2 接口总表的一致性。
- 未覆盖：真实 MySQL 交互（DDL 差异、SQL 方言）、SSE/WebSocket 端到端、浏览器 UI；lint/typecheck 仍未配置。

### Fixed — 2026-10-10（同日代码复核 + 运行验证）

- `GET /api/v1/logs/recent` 修复 500：Linux 各表缺失的 `hostname`/`source_address` 改为 `NULL AS <列>` 占位，返回中这些字段为 `null`。修复前实测报 `(1054, "Unknown column 'hostname' in 'field list'")`。
- 分类关键词查询不再对缺列表拼接固定列名：`keyword` 只在目标表实际存在的 `hostname`/`source_address`/`type` 交集上做 `LIKE`。修复前 `process_command_execution`、`file_object_access`、`network_ipc_communication`、`system_service_audit_lifecycle`、`security_policy_config_change` 带 `keyword` 时均返回 500。
- `recent_rows()` 的排序键改为统一 ISO 字符串（`_event_time_key()`）：两个库的时间列类型尚未核验，datetime 与字符串混用时会抛 `TypeError`，现在按时间先后排序且缺失值排在最后。
- `GET /api/v1/logs/export` 不再被分类列表接口的 100 行上限静默截断：`services.query_category()` 新增 `size_cap` 参数（列表接口保持 100），导出显式传 10000。实测 `size=100/1000/10000` 分别返回 100/1000/10000 行。
- 分类查询的 `category` 校验收紧为仅接受 ASCII 字母、数字和下划线；修复前中文等非 ASCII 字符能通过 `str.isalnum()` 校验并被拼进表名，实测返回 500，现为 400 `invalid category`。
- `POST /api/v1/logs/{platform}` 的 SSE 摘要改为优先读取 `normalized` 子对象字段（`category`/`event_time`），旧格式回退到顶层 `category`/`channel`/`Channel`/`event_time`/`TimeGenerated`/`timestamp`；数组元素不是 JSON 对象时不再在实时事件阶段抛 500，改为跳过持久化并返回 `persisted:false`。

### Verified — 2026-10-10（只读库核验与接口冒烟）

- 只读核验 7 张 Linux 表列名：均拥有 `event_time`/`category`/`type`/`result`；`hostname` 与 `source_address` 的缺列分布已记入 `DATABASE.md` §4.3。
- 只读核验 5 张 Windows 表列名：与代码依赖完全一致。
- 只读核验 `log_fusion`：仅存在 `client_status`，列与 ORM 模型一致。
- 只读核验：`linux_logs` 与 `log_fusion` 中均不存在 `audit_log_evidence`，存证三条接口在本环境必然返回空集合。
- 接口冒烟（独立实例，未写入任何数据）：12 个类别的列表与关键词查询、`/recent`、`/summary`、CSV 导出的 100/1000/10000 行、SSE（`connected` → 空闲 20 秒 `heartbeat`）、WebSocket（建连即快照，且 3.7 秒后再次收到周期快照）、`/health`、`/`、`/partials/db-status`、`/api/v1/evidence/*` 均符合文档描述；未执行心跳与日志写入请求。
- 端到端计时（本机）：`/` 273 ms、`/api/v1/logs/summary` 234 ms、分类列表 470 ms、`/partials/db-status` 21 ms；**`/api/v1/logs/recent?limit=20` 约 4.1 秒**，原因是 7 张表 UNION 后整体排序，尚未优化（该接口当前未被页面调用）。

### Documentation — 2026-10-10（同日按当前代码复核）

- 修正文档与代码相反的描述：页面已接入 `/ws/client-monitor` 且 `wsStatus` 已定义；watchdog 每 5 秒无条件广播快照（仅状态变化时才写库）；`启动模拟请求.ps1` 已指向 `backend/`；活代码、规则文件、`docs/` 与启动脚本中已无 `python/` 过期路径。
- 补充此前未记录的实现：静态资源缓存策略、模拟器 `--data-dir`/`--clients` 参数、导出的单次取数上限、非对象采集载荷行为、单连接广播超时与保活方式、SSE 的 20 秒心跳与 15 秒 `: ping` 注释帧。
- 新增 ADR-009（心跳快照周期广播与前端假死检测）、ADR-010（分类列表与 CSV 导出使用不同取数上限）、ADR-011（前端依赖本地自托管）。
- `AGENTS.md`、`AGENT_RULES.md`、`docs/README.md` 更新测试运行方式、自托管依赖约束与数据库隔离约束。
- `TODO.md` 移除已完成的模拟脚本路径、文档路径与日志查询字段假设条目，改写 WebSocket、CSV、测试与待确认条目。

### Documentation — 2026-10-10（基线建立）

- 根据当前 `backend/`、`frontend/`、启动脚本和遗留契约建立文档基线。
- 新增项目总览、系统架构、数据库、API、技术决策和待办文档。
- 完善 `AGENT_RULES.md`，明确代码边界、数据库/API 文档同步、PowerShell BOM、验证和交付要求。
- 记录心跳已持久化到 `log_fusion.client_status`，而 SSE 队列和 WebSocket 连接仍为进程内状态。
- 记录数据库 DDL 未纳入迁移、存证接口仅为读取兼容层等已知限制。

### Changed（接口行为汇总）

- 无数据库结构变化（验证期间的库访问全部为只读；应用启动时的 `create_all` 对已存在的 `client_status` 不产生变更）。
- 接口行为变化：`/recent` 缺列占位与排序键、关键词列适配、导出条数上限、类别字符校验、SSE 摘要字段来源；静态资源缓存头按路径分策略。

## 历史记录待整理

当前未建立可信的已发布版本与日期映射。后续发布时从 `Unreleased` 拆分正式版本，并基于 Git 提交或发布记录补充历史，禁止依据旧规划文档推测版本。
