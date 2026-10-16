# 变更日志

本文件记录当前 FastAPI 实现的可追踪变化，格式参考 Keep a Changelog。由于现有 Git 历史和旧文档不足以可靠还原所有版本，暂不编造历史版本号。

## Unreleased

### Fixed — 2026-10-16（更新单元在旧 git 上秒退 129，且日志无痕）

- 现象：页面上点更新后，`journalctl -u log-fusion-update` 只有一行 `code=exited, status=129`，脚本自己的 `/var/log/log-fusion-update.log` **根本不存在**，完全看不出原因；服务本身未受影响（失败发生在停服之前）。
- 根因：`deploy/update-log-fusion.sh` 全程使用 `git -C "$APP_DIR"`，而服务器上的 git 是 CentOS 7 自带的 **1.8.3**（`-C` 需要 ≥ 1.8.5）。git 遇到用法错误固定以 **129** 退出，所以第一条 `git -C` 就让脚本当场中断。
- 日志为什么没留下：脚本用 `exec > >(tee -a "$LOG_FILE") 2>&1`，而 systemd 单元默认 `KillMode=control-group`——主进程一退出就把它派生的 `tee` 一起杀掉，日志文件甚至来不及创建，journal 里也没有输出。
- 修复：
  - 脚本改为**先 `cd "$APP_DIR"` 再用普通 `git`**，去掉全部 `git -C`；远程地址改用 `git config --get remote.origin.url`（`git remote get-url` 需要 git 2.7+）。
  - 日志改为脚本**自己直接追加写文件**（不再用 `tee`），并加 `set -E` + `ERR` trap：任何未预期的失败都会在日志里留下**退出码和失败命令**。
- 测试：新增 `test_update_script_avoids_git_options_absent_in_old_git`（断言代码中不含 `git -C` / `remote get-url`，且必须有 `cd "$APP_DIR"` 与 ERR trap），用例数 288 → 289。
- 无实际数据库结构变化，无实际接口行为变化。

### Fixed — 2026-10-16（更新脚本可能永远拉不到新代码）

- 现象：服务器上 `git fetch --prune origin main` 明明成功、`git reset --hard origin/main` 却停在克隆时的旧提交（`HEAD is now at 6a36875`）。
- 根因：refspec 只写 `origin main`（无冒号）时，是否顺带更新 `origin/main` 取决于该仓库有没有配 `remote.origin.fetch`；配置缺失时只写 `FETCH_HEAD`。而脚本随后用 `git rev-parse origin/main` 取新版本，读到的是克隆时的旧 ref → 判定「远端无新提交」→ **永不更新，页面按钮还看起来是成功的**（静默失效）。
- 修复：`deploy/update-log-fusion.sh` 的 fetch refspec 显式写成 `+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH`，不再依赖仓库配置；并在 `NEW_HEAD == OLD_HEAD` 时用 `FETCH_HEAD` 交叉核对，不一致就报错中止而不是静默跳过（`docs/DEPLOY.md` §3 同步）。
- 同类的静默跳过一并修掉：原来 `[ -x "$VENV_DIR/bin/pip" ]` 为假时，即使 `requirements.txt` 有变化也会**一声不响地跳过装依赖**（venv 用 `--without-pip` 建的就中招）。现在改为明确告警并提示 `python -m ensurepip --upgrade`。
- 测试：新增 `test_update_script_fetches_into_remote_tracking_ref`，用例数 287 → 288。
- 无实际数据库结构变化，无实际接口行为变化。

### Changed — 2026-10-16（启动仅补充最新安全日志，停止历史追补）

- 用户确认现有历史导入量足够后，backend 固定使用 live_only 模式；不再调用三个月历史初始化或选择旧历史 pending。每次启动重设近期实时起点，后台默认开启，本机 .env 也已开启。
- 默认首次回看 120 秒，每 30 秒巡检，延后最新 30 秒；连接中断时最多回看 600 秒，超过范围的旧 live 队列裁剪，不自动补停机期间所有历史。保留入库记录、计数和历史窗口，未完成历史标 stopped，不能误标已导完。
- CLI run 默认仅实时，历史必须显式 --history-only；--until-complete 仅允许与历史模式组合。前端显示历史已停止、仅追加最新数据，既有每 10 秒刷新与 WS 保留。
- 无实际数据库结构变化；摘要接口增加 collectionMode/liveWindowSeconds 及 stopped/disabled 状态说明，无新增路由。新增针对启动、长中断、队列裁剪和不回退历史的隔离测试。
- 验证：287 项测试、两项图表自检通过；本机重启后摘要确认 live_only，三类近期巡检成功，IPS 首轮追加 58 条，原有 8 个历史窗口保持 stopped。


### Added — 2026-10-16（三类安全日志建表、可续跑补录和前端查看）

- 实际创建 log_fusion 三张安全日志表和 nsfocus_sync_state，保留原表。新增 backend/app/security_logs.py 归一化、身份去重/冲突检测、时间二分、事务断点、网络退避与会话锁；独立 CLI import_history.py 支持迁移、最近三个月补录、续跑和状态。
- 新增三条只读 security API、#/security 页面和既有 WS 的 security_logs 通知，页面每 10 秒更新，原始日志以文本显示。新增配置默认关闭采集，单 worker；不引入 SSE。
- 根据用户最新要求停止自动采集，本机开关已关闭，保留已导入 IPS 733 / WEB 9,284 / 黑名单 0 条及断点。10:12:03 北京时间只读核验近三个日历月匹配 IPS 6,008,700 / 黑名单 6,725 / WEB 18,946，共 6,034,371；不是已入库总量，完整历史由用户运行命令导入。
- 添加不访问数据库的采集边界/去重/错误路径及 API 用例，迁移、配置、命令和限制见 security-log-client/docs/SYNC.md。保留另一项部署改动，将 README/本文件既有冲突两侧内容合并；未改 Git 合并索引。


### Documentation — 2026-10-16（三类安全产品日志使用 log_fusion 分表）

- 按用户要求提供 `security-log-client/sql/log_fusion_nsfocus_tables.sql`，设计 `log_fusion.nsfocus_ips_log`、`log_fusion.nsfocus_ip_blacklist_log`、`log_fusion.nsfocus_web_security_log` 三张独立日志表；没有在数据库执行。
- 对照真实 API 及已核验 CSV 表头，独立保存 110/62/84 个来源字段，外加各 8 个本地管理字段；提供完整字段映射及 `docs/LOG_FUSION_TABLES.md`。API 实测字段分别只有 70/62/64，CSV 扩展列并不代表 API 已返回这些字段。
- 使用实例+索引+log_id 唯一键、UTC 查询时间列、网络/规则索引及完整 JSON，原始时间和数值口径分开。旧通用四表方案标为历史参考。说明兼容性、容量、入库约束及上线/回滚顺序。
- 无实际数据库结构变化，无实际接口行为变化；未实现数据库导入、未改已有表、应用或存证逻辑。

### Changed — 2026-10-15（安全产品日志日历月窗口探测）

- `security-log-client/probe_log_types.py` 新增 `--months 1 3`，固定截止时间按北京时间回溯日历月，正确处理月末、闰年和跨年；每类独立查询全部指定窗口，不因短窗口有数据而跳过较长窗口。
- 实机完成 24 类×2 窗口共 48 个查询窗口（含一次连接重置重试，共 49 次请求）：入侵防护一个月/三个月为 1,870,861/6,010,498，恶意 IP 全局黑名单为 903/6,718，WEB 安全为 6,222/18,826；其余 21 类两个窗口均为 0，报告保留准确范围、失败历史和样例。
- 无实际数据库结构变化，无实际接口行为变化；仍是外部接口只读查询和本地保存。

### Added — 2026-10-15（截图日志类型逐类实机探测）

- 独立安全产品项目新增 `probe_log_types.py`，复用连接文件，读取设备目录后逐类验证截图完整可见的 24 种日志。固定时间范围先查 24 小时，必要时查 7 天；每次仅取 1 条，报告和完整响应逐类保存。
- 实机确认入侵防护、恶意 IP 全局黑名单、WEB 安全三类有真实样例，24 小时匹配数分别为 66,486、64、440；其余 21 类在本账号两个窗口中均为 0。一次防火墙连接重置重试后恢复；错误保留，不伪装为空数据。
- 区分有样例、部分结果、窗口内为 0、查询失败/类型未核实；不将目录 isvalid 当作有数据，也不将单条字段数当作完整字段合同。无实际数据库结构变化，无实际接口行为变化。

### Documentation — 2026-10-15（安全产品字段核验与入库草案）

- 核对独立项目 3,000 条日志：source 并集 50 个字段、单条 46～50 个；事件响应为空，无真实事件样例。手册事件成功示例有 25 个字段，标为待实测。
- 新增 `security-log-client/docs/DATABASE.md`（含完整实测字段表）和未执行的 `sql/schema_draft.sql`，设计批次、日志、事件及关联四表，使用常用投影列 + 完整 JSON、来源隔离和上游 ID 幂等键。
- 只交付设计，没有实现导入器，没有连接数据库或执行 DDL/DML。无实际数据库结构变化，无实际接口行为变化。

### Changed — 2026-10-15（安全产品客户端独立项目与样例分析）

- 将两个请求脚本、接口专题文档、真实连接文件及历史调试数据统一迁入与 `backend/` 同级的 `security-log-client/`；原 tools/ 入口已移走，主文档仅保留项目导航。
- 项目自带 README、接口文档、占位配置、元数据和 Git 忽略规则；默认配置为项目内 `config/connection.json`，默认新响应位于 `data/captures/`，不再依赖主项目路径。
- 实机重新拉取 3 页各 1,000 条 Windows 日志，共 3,000 条，及事件查询空响应。新增标准库 `analyze_samples.py` 输出 JSONL 样例、统计 JSON 和中文分析，保留抽样边界、原文比对及时间口径说明。
- 无实际数据库结构变化，无实际接口行为变化；没有导入 Log Fusion 数据库。

### Added — 2026-10-15（绿盟事件联调、本地凭据及批量日志）

- 经用户明确要求将密码及 Token 保存到已忽略的 `security-log-client/config/connection.json`，新增标准库 CLI `security-log-client/nsfocus_api.py` 读取连接文件；支持登录保存 Token、HTTP 401 刷新一次、事件查询、辅助目录、关联日志查询及分页批量拉取。
- 实机确认 3.5.1 必须带 `conditionStr=[]`；省略返回 400/10000。带空条件查询最近 24 小时及 7 天均返回 200/0 条。3.5.2/3.5.3/3.5.4/3.5.6 均成功；3.5.5 缺少事件 ID 返回 400/28520，成功分支待真实事件验证。
- 实测日志每页 1,000 条、连续两页共 2,000 条，两页无重复，分别约 5.9/7.5 秒。批量顺序请求、固定时间范围、默认间隔 0.2 秒，逐页落盘并记录 manifest，异常或部分结果时停止；没有自动执行百万条全量下载。
- 新增 `security-log-client/docs/API.md` 说明运行方式、结果与分页边界。无实际数据库结构变化，无实际接口行为变化。
### Added — 2026-10-16（systemd 服务安装脚本，注册服务不再只有手工命令）

- 新增 `deploy/install-service.sh`：把 `deploy/log-fusion.service` 安装到 `/etc/systemd/system/` 并 `daemon-reload` + `enable` + `restart` + 30 秒 `/health` 自检。**幂等**，可重复执行；单元内容有变化时先把旧文件备份成 `.bak` 再覆盖。执行前校验 root、`systemctl`、单元文件、`.venv/bin/python` 可执行、`backend/.env` 是否存在，并在 8080 被「非本服务的进程」占用时明确提示先停掉挂在 SSH 前台的 `run.py`。
- `deploy/update-log-fusion.sh`：更新后若 `deploy/log-fusion.service` 在本次版本区间内有变化，打印「请重跑 install-service.sh」提示（脚本不替你改系统单元）；`systemctl start` 返回非零时改为继续等 `/health` 并给出排查线索，`git reset --hard` 失败时会先拉起服务再中止。
- 新增契约测试：`log-fusion.service` / `install-service.sh` / `update-log-fusion.sh` 三处的服务名必须一致（对不上时 `systemctl stop` 属于静默失败），用例数 259 → 260。
- `docs/DEPLOY.md`：§2 第 4 步改用安装脚本并说明其幂等性；§3 补充「单元变化」步骤；§5 排查表新增 4 行。
- 无实际数据库结构变化，无实际接口行为变化。

### Added — 2026-10-15（服务端一键更新：systemd 单元 + 更新接口 + 前端按钮）

- 新增 `deploy/log-fusion.service`：把服务注册成 systemd 单元（`WorkingDirectory=/opt/log-fusion/backend`、`PYTHONPATH` 指向 backend、`ExecStart=<venv>/python run.py`、`Restart=always`、`KillSignal=SIGINT`），从此用 `systemctl start/stop/restart log-fusion` 管理，不再需要挂 SSH 前台终端。
- 新增 `deploy/update-log-fusion.sh`：一键**原地**更新——校验是 git 工作树 → 备份服务器上未提交的改动为 patch → 停服 → `git fetch --prune` + `git reset --hard origin/main`（带超时兜底）→ 仅在 `requirements.txt` 有变化时装依赖 → 重启 → 最多 30 秒轮询 `/health` 自检；用 `flock` 防止并发更新。`.venv`/`backend/.env`/`data`/`backend/data` 都在 `.gitignore` 里，git 不会改动未跟踪/被忽略的文件，因此不再需要「搬出 → 删目录 → 重 clone → 搬回」，也**绝不使用 `git clean`**（见 ADR-020）。
- 新增接口 `POST /api/v1/admin/update`（需 `confirm=true`）：把脚本复制到仓库外的 `/usr/local/bin/log-fusion-update.sh`，再用 `systemd-run --unit=log-fusion-update --collect` 起一个**独立**的 transient 单元执行后立即返回；已有更新在跑返回 409，服务器无 `systemd-run` 返回 500。用独立单元而非 `setsid`/`nohup`，是因为 systemd 停服会清理整条 cgroup，普通后台进程会被一起杀掉导致更新中断（详见 `docs/DEPLOY.md`）。
- 前端新增「系统维护」页与「⇩ 拉取最新代码并重启服务」按钮：二次确认后触发接口，随后轮询 `/health`，服务恢复即自动刷新页面。
- 新增 `docs/DEPLOY.md`：首次注册 systemd 服务、目录约定、两种更新方式、排查表与安全提醒。`app.js?v=` → `20261015-1`。
- `.gitignore` 增加 `.venv/`（原地更新的前提是运行期文件都被忽略）；`tests/test_main_contracts.py` 新增两条契约测试，锁定「脚本只用 `reset --hard`、不含 `git clean`/`rm -rf`」与「`.gitignore` 必须忽略 `.venv/`/`.env`/`data/`/`backend/data/`」。
- 无实际数据库结构变化；接口行为：仅新增 `POST /api/v1/admin/update` 一条路由（见 `docs/API.md` §8.1），其余接口不变。

### Added — 2026-10-15（绿盟用户日志查询脚本）

- 新增独立标准库工具 `security-log-client/nsfocus_log_search.py`：按用户提供的 API 手册 §3.1 登录获取 Token，按 §3.4.1 查询一页用户日志，支持已有 Token、时间范围、分页、查询字符串、JSON 保存及离线请求预览。
- 默认 HTTPS 证书验证、直连内网、拒绝重定向，提供私有 CA 与显式测试跳过验证选项；报告 HTTP/业务错误及部分结果提示。
- 经用户授权实机验证：设备使用自签名证书，登录成功；最近 24 小时 `windows_log` 查询成功返回 10 条，约耗时 10.8 秒。未限定时间范围的首次请求读取超时，README 增加近期时间范围示例。调试响应仅存于已忽略的 `security-log-client/data/debug/`，不保存密码或 Token。
- 无实际数据库结构变化，无实际接口行为变化；工具不接入 Log Fusion 服务，不导入数据。

### Fixed — 2026-10-15（Windows 日志全部无存证，导致存证总量远小于日志总量）

- 现象：日志总量 713,395，完整性存证只有 42,702，且存证表 `platform='windows'` 为 **0 条**。
- 根因：`hashing.build_evidence_row` 只认载荷里的 `log` 字段，而 Fluent Bit winlog 载荷没有 `log`（只有 `TimeGenerated`/`EventID`/`Channel`/`Data`），于是每条 Windows 日志都命中「缺少 log 字段」分支被**静默跳过存证**——日志照常入库、接口照常返回 200，只有 `errors[].stage="hash"` 计数，无人查看。441,408 行 Windows 业务数据全部如此，占缺口 65.8%。
- 修复：`hashing.py` 新增 `canonical_json()` 与 `evidence_input()`，按平台分流取存证原文——Linux 继续用 `log` 原文（保持不变，已有 42,702 条存证的摘要不受影响），Windows 改用整条记录的规范化 JSON（键排序 + 紧凑分隔符 + 非 ASCII 不转义）；时间戳按 `TimeGenerated` → `TimeWritten` → `event_time` → `timestamp` → `date` 取原始字符串，优先级与 `parsers.normalize_windows` 一致。`source` 列对 Windows 回退取 `SourceName`，避免台账「来源」全空。
- 校验端 **无需改动**：`raw_log` 存的就是被哈希的字符串本身，`EVIDENCE_VERIFY_SQL` 的 `SHA2(CONCAT(IFNULL(log_timestamp,''), CHAR(31), raw_log), 256)` 原样适用。
- 已知边界：Windows 的 `raw_log` 是服务端反序列化后规范化的形态，不是采集端字节流，因此其存证证明「服务端收到的事件内容」而非「采集端到服务端的传输未被篡改」。要覆盖后者需改采集端输出带 `log`，已登记 `docs/TODO.md`。
- 存量：2026-10-15 之前入库的 Windows 日志未保存原文，**无法补建存证**；经确认按「只修往后」处理。Linux 侧另有约 229,285 行早于存证功能（2026-10-11 15:19）上线的历史数据同样无存证——因此两个指标短期内仍不会相等。
- 测试：`tests/test_hashing.py` 新增 11 个 Windows 用例（规范化 JSON 稳定性、键序无关、非 ASCII 不转义、任一字段变更都改变摘要、时间戳优先级、空对象跳过、与校验端 SQL 复算口径对齐、`source` 回退、Linux 口径不得放宽），全套 **251 个用例通过**；既有 240 个用例一字未改。
- 文档：`docs/DATABASE.md` §3.2（哈希输入定义 + 覆盖率历史基线）、`docs/API.md` §5 与 Windows 样例、`docs/ARCHITECTURE.md`、`docs/README.md`、`AGENTS.md`（含测试数 210 → 251 的过期修正）、`docs/DECISIONS.md` 新增 **ADR-018**、`docs/TODO.md`。

### Documentation — 2026-10-14（统一日志编码）

- 明确日志从采集、JSON/JSONL、HTTP 到前端展示统一使用 UTF-8；数据库连接对应 `utf8mb4`，既有表/列字符集仍需实测核验。
- 检查当前页面 UTF-8 声明、前端 `Response.json()` 解码及 Alpine `x-text` 日志渲染链路；无需修改前端代码。说明 Unicode 转义与乱码的区别，以及 CSV/PowerShell 5.1 的 BOM 例外。

### Changed — 2026-10-14（采集错误控制台诊断）

- 日志采集解析、业务入库、存证失败时，在 `log_fusion` ERROR 日志中打印批次计数与每条错误的平台、索引、客户端、阶段、表名和原因；HTTP 响应截断不影响控制台诊断。
- 采集请求体校验失败（422）、请求体解码失败（400）等接收异常记录路径、来源地址、状态码与错误原因，不打印完整请求原文；原有 HTTP 状态码与响应契约保持不变。
- 修正无记录索引的存证批次错误在排序时引发 `KeyError` 的问题。

### Fixed — Windows Fluent Bit 事件字段归一化

- 兼容 Windows Fluent Bit 同时发送 `Data: ""` 与非空 `StringInserts` 的记录：解析时回退使用 `StringInserts`，避免事件参数丢失。
- Windows 时间字段继续支持 `TimeGenerated`、`TimeWritten`、`event_time`、`timestamp` 和 epoch `date`。

### Changed — 2026-10-13（存证中心 P0：功能分区 + 平台/时间筛选）

按「企业项目要有合理功能规划」的要求，把存证页从「一个长面板」改成按职责分区的存证中心，
并补齐审计排查最基础的两个筛选维度。

- **存证中心四个功能区**（`evidenceTab`）：存证总览 / 存证台账 / 归档管理 / 算法与策略。
  此前列表、篡改告警、导出和**会删除数据的归档操作**全堆在同一个面板里，误触成本高；
  现在归档独立成区，算法与安全边界也从表格下方的小字提升为独立分区。
- **新增入库时间范围筛选**：`/api/v1/evidence` 与 `/api/v1/evidence/export` 新增 `start`/`end`
  （`YYYY-MM-DD`，按 `created_at`，`end` 含当天 23:59:59，格式错误 400）。
  用**入库时间**而非 `event_time`：补传的历史日志事件时间可能很早，按入库时间才能回答
  「这段时间系统实际存了什么」。
- **平台筛选接入页面**：后端此前已支持 `platform` 参数但页面从未传过，现在台账区有平台下拉
  （取值 `linux`/`windows`，与存储口径一致）。
- **导出改为「导出当前筛选」**：导出参数与列表共用同一组状态（校验状态/关键词/平台/日期），
  不再写死 `href="/api/v1/evidence/export"`，避免导出内容与页面所见不一致；新增「重置」按钮。
- **移除概览存证面板残留的 `PENDING` 元素**：该状态已在篡改校验改造中移除，但模板仍渲染
  「待校验 N 条」且 `hashBar()` 仍返回 `pending` 占比，属于永远为 0 的死代码，一并清理。
- 新增测试：`_evidence_where` 的平台/日期/组合/空条件用例、非法日期 400、列表与导出参数校验，
  以及前端契约（四个分区存在、平台与日期控件、导出复用筛选、重置入口）。测试总数 223 → 234。
- 实测（2026-10-13，真实库）：日期筛选精确划分全表 —— `end=2026-10-12` 命中 34,099 条、
  `start=2026-10-13` 命中 7,023 条，两段之和 41,122 与全表一致；组合筛选
  `platform=linux + verify=HASH_MISMATCH + 当日` 精确返回 3 条 `tamper-demo` 异常；
  导出同一条件得到 3 行 CSV 且带 `verify_status` 列。
- 浏览器实测（headless Chrome + CDP）：四个 Tab 逐个点击均只显示对应分区；
  1680/1280/390 三种视口下无控件重叠、无页面级横向溢出（窄屏表格由 `table-responsive` 容器内滚动）。

### Added — 2026-10-13（存证逐条篡改校验）

用户要求「往 `log_hash` 插几条异常数据，看能不能检测出来」。结论：**此前完全检测不出来**——
`/api/v1/evidence/summary` 的 `VALID` 是 `total` 的硬编码副本，`HASH_MISMATCH`/`RAW_MISSING`
恒为 0，等于对所有篡改都报「正常」。现已实现真正的逐条复算：

- `EVIDENCE_VERIFY_SQL`：在 SQL 侧按 `sha256(IFNULL(log_timestamp,'') + 0x1F + raw_log)` 复算，
  与 `log_hash` 比较（`<=>` 做 NULL 安全比较），分类为
  `VALID` / `HASH_MISMATCH`（原文、时间戳或摘要被改）/ `RAW_MISSING`（原文为空）。
  放在 SQL 里是因为 `raw_log` 是 LONGTEXT，拉到 Python 再算会把整表原文读进内存。
- **校验规则必须与写入端完全一致**：分隔符同为 `0x1F`；NULL 时间戳按**空串**参与哈希
  （写入端 `raw_text(None) == ""`）。曾经把「时间戳为 NULL」定为 `PENDING 待校验`，
  结果把一条由本系统正常写入的「无时间戳」存证报成异常——**校验规则与写入规则不一致会制造假警报**。
  该分类已移除。
- `EVIDENCE_STATUSES` 由 4 个收敛为 3 个（`PENDING` 不可达，保留只会永远是 0 或产生假警报）。
- `/api/v1/evidence/summary` 响应新增 `healthy`（两类异常都为 0）与 `source`
  （实际读取的 host / 库 / 表，用于排除「页面和 Navicat 看的不是同一个库」这类误判）。
- `/api/v1/evidence` 每行新增 `verify_status`；新增 `verify` 筛选参数
  （`VALID`/`HASH_MISMATCH`/`RAW_MISSING`，其他值 400），可直接筛出被改动的记录。
- `/api/v1/evidence/export` 同样新增 `verify` 参数与 `verify_status` 列，便于在 Excel 里筛异常。
- 前端存证页：新增「校验结果」列（一致/哈希不一致/原文缺失 三色徽章）、校验状态筛选下拉、
  关键词查询框、顶部篡改告警条（有异常时显示条数）、页脚显示实际数据源；
  指标改为 存证总数 / 校验一致 / 哈希不一致 / 原文缺失。概览页存证面板的说明文字同步更新
  （原先写着「逐条复算需要手工执行，接口暂不提供」，已过时）。
- 新增测试：校验规则与写入端一致性（分隔符、NULL 时间戳按空串、NULL 安全比较、状态集合无
  `PENDING`）、`verify` 参数校验（合法值 503 / 非法值 400）、`source` 与 `healthy` 字段、
  以及 `hashing` 侧锁住「缺时间戳按空串哈希」的规则。测试总数 210 → 223。
- 实测（2026-10-13，真实库 39,924 行）：全表校验 **240 ms**。插入的 5 条异常被正确分类——
  原文篡改 / 哈希替换 / 时间戳篡改 → `HASH_MISMATCH`；原文清空 → `RAW_MISSING`；
  其余 39,920 条真实数据全部 `VALID`，无误报。
- **已知边界**：`sha256(内容)` 只能证明「库里的原文与摘要自洽」，**不能证明该记录当初真的
  来自客户端**。任何能写库的人都可以造一条原文与摘要都自洽的假记录，这种篡改检不出来
  （实测第 5 条演示数据即为此类，被判为 `VALID`）。要防伪造需要把摘要改为 HMAC/签名
  （密钥只在服务端）或写到客户端无法触及的位置，见 `docs/TODO.md`。
- 性能与扩展：当前校验是每次请求的整表扫描，万级约 0.2 秒；百万级需改为增量校验或异步任务。

### Changed — 2026-10-13（平均延迟线收细到 2px）

用户要求「平均延迟的线条细一点」：心跳延迟趋势里平均延迟的 `lineStyle.width` 从 **3px → 2px**，
投影也从 `blur 8 / alpha .35` 收到 `blur 6 / alpha .30`（投影会把细线视觉上加粗，一并收一点）。

- 线宽层级因此是 **1px（客户端细线，opacity 0.35）/ 2px（平均延迟，主视线）**：平均线仍是图上唯一带渐变面积的序列，主次靠面积与线宽共同区分，不再靠「很粗的线」。
- 自检新增断言：平均延迟线必须**比客户端细线粗且不超过 2px**（`tools/chart-render-check.cjs`），防止以后又调回 3px。`app.js?v=` → `20261013-11`。

### Changed — 2026-10-13（按需求取消延迟曲线平滑，改回原始折线）

用户要求「取消平滑效果，直接上折线的效果」。上一轮加的四层平滑（EWMA + 居中滑动平均 + 样条单调约束）已**整体移除**，延迟趋势按 5 秒采样的原始值直接画折线：

- `app.js` 删除 `smoothLine()` / `movingAverage()` / `smoothSeries()` 三个函数与 `DELAY_SMOOTH` / `DELAY_MA_HALF` 两个参数（不留死代码，历史版本可在 git 里找回）；保留 `DELAY_AXIS_FLOOR`。
- 平均线与客户端线都改回 `smooth:false`（直线段连接），并移除 `smoothMonotone`；平均线仍按「图中未掉线客户端的原始采样值」现算（不读接口的 `avgDelay`），也**不做平滑**。
- **保留的视觉部分不变**：渐变面积主序列、客户端细线（opacity 0.35，hover 加粗）、统一的十字准线 tooltip、纵轴锚定档位（`niceDelayMax()`，下限 5 秒）、延迟逼近 10 秒时的 `延迟阈值 10s` 参考线。图下说明改为「按原始采样值绘制折线（未做平滑）」。
- 自检与测试同步**反转**：`tools/chart-render-check.cjs` 原来断言「4↔20 秒抖动必须被压到 < 2 秒」，现在改为断言「客户端曲线与原始采样**逐点一致**、相邻跳变如实为 16 秒、`smooth:false`」——这条断言同时是「平滑有没有被偷偷加回来」的探针；`tests/test_frontend_contracts.py` 的 `test_delay_chart_is_smoothed` 换成 `test_delay_chart_plots_raw_polyline`（断言三个平滑函数都不存在、折线为 `smooth:false`）。用例总数不变（210），`app.js?v=` → `20261013-10`。

### Changed — 2026-10-13（心跳延迟趋势改为两级平滑，实测相邻跳变 4.0 → 1.0 秒）

用户反馈「还是不够平滑」。原因不在采样，而在**单级 EWMA 只能削峰**：它把单点尖峰压下去，但序列仍会一级一级地爬和落，看起来依旧毛糙；另外 `smooth` 值偏高时 ECharts 的样条会在拐点**过冲**，产生肉眼可见的波浪。这一轮把平滑做成四层（参数集中在 `app.js` 的 `DELAY_SMOOTH` / `DELAY_MA_HALF` / `DELAY_AXIS_FLOOR`，要更稳或更灵敏只改这三个数）：

1. **先按原始值求平均，再平滑主曲线**（原来是对已平滑的各条线再平均，等于把每台机器残留的起伏带回了主曲线）；
2. EWMA α 0.25 → **0.2**（≈25 秒等效窗口）；
3. 新增 **居中滑动平均** `movingAverage()`（±3 个采样点 = ±15 秒），与 EWMA 串成 `smoothSeries()` 管线；居中所以**不引入滞后**，这是本轮最关键的一层；
4. 线条改用 `smooth:0.5` + **`smoothMonotone:'x'`**，禁止样条反向摆动（消掉「波浪感」）。

另外把延迟纵轴的**下限抬到 5 秒**（`niceDelayMax()` 档位从 1/2/5/10… 改为 5/10/15/20/30…）：延迟只有 1 秒时，若画布只有 2 秒高，0.2 秒的正常起伏也会占满 10% 的高度。

- 实测（`tools/chart-render-check.cjs` 的极端用例「4 秒 ↔ 20 秒交替」）：相邻点最大跳变 **4.8 秒（原始）→ 4.0 秒（仅 EWMA）→ 1.0 秒（两级平滑）**；另一组 3/12/6 秒锯齿的总起伏 **58.6 → 11.3**（压掉 81%）。
- 自检同步收紧：原来的「最大跳变 < 8 秒」改成 **< 2 秒**（去掉居中滑动平均会立刻失败），并新增量化断言「两级的总起伏 < 单级 EWMA 的 60%」；`tests/test_frontend_contracts.py` 的平滑用例改为断言**两级管线都在**（`smoothLine` + `movingAverage` + `smoothSeries`）与「先平均后平滑」的顺序。客户端细线透明度 0.55 → 0.35。`app.js?v=` → `20261013-9`。

### Changed — 2026-10-13（心跳延迟趋势改版：与概览「接入趋势」统一视觉 + 明显不抖）

- **视觉统一**：心跳页「心跳延迟趋势（最近 15 分钟）」改成与概览「最近 60 分钟接入趋势」同一套语法——主序列（平均延迟）是**渐变面积曲线**（`areaGradient('#2f6bff')`，与趋势图同一色板与渐变参数），客户端曲线细而半透明（`width:1, opacity:0.55`，hover 才加粗）退到背景；图例、网格、坐标轴、tooltip 十字准线全部复用与趋势图相同的公共片段（新增 `tooltipAxis(unit)`，趋势图传 0 个参数、延迟图传 `'秒'`）。
- **明显不抖**，三条措施（需求原文是「不要抖动很大」）：
  1. **纵轴锚定**：新增 `niceDelayMax()`，纵轴固定 `min:0` + 取 1/2/5/10/15/20/30/45/60… 的整数档上限（留 25% 余量）。**自动缩放才是「抖动很大」的元凶**——数据在 3~4 秒之间波动时，自动轴会把 3~4 撑满整个高度，看上去像剧烈震荡；锚定后同样的数据是一条平缓的带子。
  2. **平滑加强**：EWMA 系数 0.3 → **0.25**（≈20 秒等效窗口，15 分钟窗口内仍有 180 个点），并把 `smooth:0.7` 与细线宽度一起调整，自检里 4↔20 秒交替的极端抖动最大相邻跳变从 4.8 秒降到 **4.0 秒**。
  3. **阈值参考线**：平均延迟逼近 10 秒（在线/延迟分界，见状态阈值）时画出 `延迟阈值 10s` 虚线；纵轴够不着时（上限 < 10 秒）不画，避免小延迟场景被一条线压平。
- 测试与自检 209 → **210**：`tools/chart-render-check.cjs` 新增「心跳延迟趋势的视觉与『不抖』约束」一节（渐变面积、纵轴锚定与档位、细线退背景、小延迟不画参考线、延迟升高后轴抬高并出现参考线）与一个「延迟逼近阈值」完整渲染用例（参考线标签在最靠上的位置，专门跑越界检查）；`tests/test_frontend_contracts.py` 新增 `test_delay_chart_matches_overview_trend_style` 钉住这些约束。`app.js?v=` → `20261013-8`。

### Changed — 2026-10-13（概览新增「哈希存证」面板、移除写死的「系统状态」、侧栏连接卡改版）

- **运行概览新增「哈希存证」面板**，取代原先的「系统状态」清单：存证总数（数字滚动）+ 一致/哈希异常/原始缺失的占比条与计数 + 状态胶囊（`完整率 100%` / `哈希异常 N 条` / `原始缺失 N 条` / `存证库不可用`），右下角给出入口按钮「存证明细 →」。数据来自 `/api/v1/evidence/summary`，概览页每 30 秒刷新一次（`hashTimer`），手动「↻ 刷新」也会同步刷新。
  - **口径**：摘要按 `sha256(时间+原文)` 在入库前计算写入，接口不做逐条复算，因此正常状态下「哈希异常」恒为 0；真要校验需按 `DATABASE.md` §3.2 手工执行（`TODO.md` 已登记「存证逐条校验接口」）。这一点直接写在面板下方的说明里，避免把「恒为 0」误读成「已验证无篡改」。
  - **失败语义**：存证库查询失败时接口返回 503，面板此时显示「存证库不可用」并清空数字（不显示 0）——延续「库连不上」与「确实没有存证」必须可区分的既定原则。
- **移除概览里的「系统状态」板块**：那三行（Linux 数据库 / Windows 数据库 / WebSocket 实时通道）是模板里写死的 `text-bg-success` 徽标，与真实连接状态无关，属于误导性展示；连接状态改由侧栏卡片承担（其中 WebSocket 那一行本来就是 Alpine 绑定的真实状态，现移至侧栏）。
- **左侧导航下方的连接情况改为「服务连接」状态卡**（`/partials/db-status` 片段重做）：
  - 片段从一串 Bootstrap badge 改为**结构化行**：汇总行 `N/4 个库`（状态点 + `正常`/`部分可用`/`全部不可用`）+ 每个库一行（状态点、中文名 `Linux 日志库`/`Windows 日志库`/`融合库`/`存证库`、`已连接`/`不可用`）；真实库名（`linux_logs` 等）放在该行的 `title` 悬停提示里，因为侧栏只有 `col-lg-2` 宽，塞进每一行会把中文标签挤成省略号。
  - 「实时通道」行由 Alpine 渲染（`wsTone()`/`wsLabel()`/`wsHint()`，三态：已连接 / 连接中 / 已断开并降级为 5 秒轮询），卡片底部显示「数据库自检 30 秒」与本机时钟。
  - 状态点带同色光晕与 0.3 秒过渡，卡片用浅渐变底 + 虚线分隔；连接失败仍返回 HTTP 200，只改颜色与文案。
- 测试 204 → **209**：`tests/test_main_contracts.py` 新增 2 条片段契约（四个库与汇总数量正确、全部不可用时仍是 200 且四行都是「不可用」）；`tests/test_frontend_contracts.py` 新增 3 条（概览用哈希存证面板取代系统状态且不残留写死的状态行、存证查询失败不得显示 0、侧栏连接卡由 HTMX 片段 + Alpine 实时通道行组成且样式齐备）；`tools/chart-render-check.cjs` 增加一节断言（实时通道三种状态文案 + 存证面板正常/原始缺失/哈希异常/失败四种状态，含「失败时数字显示 — 而不是 0」）。`app.js?v=` → `20261013-7`。

### Changed — 2026-10-13（心跳延迟曲线改平滑 + 移除「客户端延迟排行」）

- **心跳延迟趋势图的曲线做指数平滑**：`app.js` 新增 `smoothLine()`，每条客户端曲线先过一次 EWMA
  （α=0.3、保留 1 位小数）再交给 ECharts（`smooth` 由 `true` 提到 `0.7`）。原因：延迟每 5 秒采一个点，
  网络抖动/单次超时会让相邻两点在 4 秒 ↔ 20 秒之间大跳，整张图就是一片毛刺，看不出走势。
  - 该时刻没有采样仍输出 `null`（断线不连），跨断点沿用上一个平滑值，回来时不会突跳；
  - 「平均延迟」粗线在**平滑后的**曲线上现算，与图里看到的曲线保持一致；
  - 图下说明补了一句「曲线已做指数平滑，保留走势、压掉单点抖动」。
- **移除心跳页的「客户端延迟排行」条形图**（按需求）：模板删掉 `latency-rank` 容器与 `rankNote` 文案，
  `app.js` 删掉 `drawLatencyRank()`/`latencyColor()`/`clientDelay()` 及 `drawHeartbeatCharts()` 里的调用。
  排行信息仍在下方「客户端列表」（含「延迟」列）。心跳页图表由 3 张减为 2 张，布局只剩
  「延迟趋势(8) + 状态分布(4)」一行。
- **测试**：`tests/test_frontend_contracts.py` 新增 `test_latency_rank_chart_is_removed`（守卫「删一半留下
  半成品」）与 `test_delay_chart_is_smoothed`，并改写心跳页图表清单与离线过滤断言；用例总数 202 → **204**。
  `tools/chart-render-check.cjs` 移除 4 个排行用例（25 → 21），新增「延迟曲线必须做指数平滑」一节
  （4↔20 秒抖动的最大相邻跳变必须 < 8 秒，实测 4.8 秒），重复推送一节同步去掉排行断言；
  `app.js?v=` 升到 `20261013-6`。两个自检工具与 `pytest` 全部通过。
- 文档：`README.md`（§2 能力、§6.1 测试数）、`ARCHITECTURE.md`（§4 心跳页链路与延迟图口径）、
  `PLAN-frontend-charts.md`（§0 偏差记录）。

### Fixed — 2026-10-13（图表整块重绘闪烁 + 离线客户端不再展示心跳延迟）

- **修掉「实时推送到达后整张图从 0 重新长出来」的闪烁**：原因是每次 `setOption` 都带 `replaceMerge`
  或每次都无脑重画，ECharts 会把每张图当成新图重播入场动画（`animationDuration`），而 WebSocket 每 5 秒
  就推一轮快照、概览每 2 秒刷一次汇总，于是看起来整页一直在闪。现在所有图表统一走 `app.js` 的
  `paint(id, option)` 出口：
  - **数据与布局都没变 → 一个字节都不重画**（watchdog 每轮都推快照，多数轮次数据完全相同）；
  - **只有数据变 → 合并更新**（不传 `replaceMerge`），ECharts 走 `animationDurationUpdate`（280ms）
    只做数值过渡，不再重播入场动画；
  - **只有 series 结构变**（未掉线客户端增减、平台筛选切换）才 `replaceMerge`，避免旧序列残留在图上。
  - 容器尺寸与实例尺寸不一致时才 `resize()`（resize 本身会整块重绘）；窗口缩放时也不再 resize 隐藏区块
    （那会把实例尺寸改成 0，等它再显示出来就是空白图）。
  - `getChart` 重建实例时（实例被销毁、或容器元素被换掉）会一并清掉绘制指纹，否则新实例会被
    「数据没变」判成无需绘制，留下空白图。
  - 指标卡的数字滚动同步修正：目标值没变就不重启动画（原来每 5 秒都重播一次数字滚动）。
- **掉线的客户端不再展示心跳延迟**：离线时「延迟」= 心跳年龄，只会一路涨到无意义的大值。现在
  延迟趋势图与延迟排行都只统计未掉线（ONLINE/DELAYED）客户端，并在图下注明排除了几个离线客户端；
  全部离线时显示「暂无未掉线客户端」空态。状态分布甜甜圈仍完整展示离线数量。
- **延迟趋势的「平均延迟」改为按图中（未掉线）曲线现算**：原先直接用接口的 `avgDelay`（含离线客户端的
  陈旧延迟），均值会浮在所有曲线之上，与图里看到的完全对不上。接口字段未改动，仍是展示口径的调整。
- **移除在线率仪表图**（心跳页）：在线率本身就是「在线客户端 / 总数」，已在该指标卡的 hint 中展示，
  独立一张 gauge 图属于重复信息。心跳页图表由 4 张减为 3 张，布局改为「延迟趋势(8) + 状态分布(4)」/
  「延迟排行(12)」。
- 自检工具同步：`tools/chart-render-check.cjs` 新增「重复推送同一份数据不得重画」「数值变化走合并更新」
  「新增采样点走合并更新」「结构变化才 replaceMerge」「实例重建后必须重画」「离线客户端不进入延迟图/排行」
  「全部离线显示空态」等断言（用例 24 → 25），
  并移除了在线率仪表用例；`tests/test_frontend_contracts.py` 新增 3 条守卫（只允许 `paint` 内部出现一次
  `setOption`、在线率图表确已移除、延迟图必须过滤离线客户端）。用例总数 199 → **202**；`app.js?v=` → `20261013-5`。

### Added — 2026-10-13（实时动态图表看板 + 融合日志二级导航）

方案与决策见 `PLAN-frontend-charts.md`（三个新接口见 `API.md` §4.6/§4.7/§6.7，架构决策见 ADR-016/ADR-017）。

- **后端新增 3 条聚合接口**（接口总数 22 → 25），全部带服务端 TTL 缓存并输出 UTC 时间桶：
  - `GET /api/v1/logs/trend`：按时间桶统计接入量（Linux/Windows 分列 + 合计），空桶补 0；
  - `GET /api/v1/logs/stats`：窗口内 TOP 日志类别与 TOP 主机；
  - `GET /api/v1/monitor/history`：心跳历史序列（进程内环形缓冲，不查库）。
  - 新增 `backend/app/metrics.py` 承载聚合与缓存；非法参数（未知 `range`/`bucket`/`platform`、点数超上限）在查库前返回 400，查询失败返回 500 而不是「全 0 趋势」。
- **运行概览**新增「最近 60 分钟接入趋势」堆叠面积图（10 秒轮询）与「平台占比」半环仪表（中心为日志总量），四张指标卡改为数字滚动；原环形图布局未改动。
- **客户端心跳监控**新增 4 张图：15 分钟延迟趋势（每客户端一条细线 + 全局平均粗线）、在线率仪表（≥95% 绿 / 80–95% 橙 / <80% 红）、状态分布甜甜圈、客户端延迟排行（<1s 绿 / <3s 橙 / ≥3s 红）。WebSocket 断开降级为 REST 轮询时，这些图走同一条 `applyCounts()` 路径继续更新。
- **左侧导航改为二级菜单**：「融合日志管理」下新增子菜单「日志检索 / 日志统计图表」，父项在子页高亮并可展开折叠；五个页面都有了 URL hash 深链（`#/log-stats` 等），刷新后停在原页面。
- **新增子页「日志统计图表」**（30 秒自动刷新）：接入时间趋势、平台占比甜甜圈、24 小时×星期热力图（前端按本地时区把 7 天小时桶聚合成 7×24 矩阵）、TOP 主机排行、TOP 类别排行，外加平台/时间范围筛选器与手动刷新。
- **前端图表基础设施**：`charts` 实例注册表 + `getChart()`（复用实例、容器不可见时跳过）+ `resizeAll()`（150ms 防抖）+ `pagehide` 时 `disposeCharts()`（此前每次刷新都会泄漏 ECharts 实例）；所有新轮询统一「页面可见 + busy 标志 + 8 秒超时」。
- **新增两个不依赖浏览器的图表自检**：`tools/chart-render-check.cjs`（24 个用例断言 10 张图都能渲染、空数据必须画出「暂无」、文字不被顶出画布、热力图恰好 168 格，`--live=<url>` 可用真实接口数据再跑一遍）与既有 `tools/chart-layout-check.cjs`（环形图布局）配套；`app.js?v=` → `20261013-4`。
- **测试 168 → 199 个用例**（`pytest --collect-only`；其中 21 个归档用例需要 `tmp_path`）：新增 `tests/test_metrics.py`（21 个：参数校验先于查库、UTC 桶键、TTL 缓存命中不重算、失败返回 500、环形缓冲窗口过滤、watchdog 接线守卫）与 `tests/test_frontend_contracts.py`（10 个：**模板里的每个图表容器都必须有对应的 `getChart()`**——正是这次「模板先加、JS 没跟上」的半成品状态；以及图表实例释放、隐藏容器跳过、UTC 转本地、二级导航与深链、自检工具已登记文档）。
- **Phase 0 只读核验（2026-10-13）**：12 张表的时间列都已有 `idx_event_time`/`idx_time_created` 索引（MySQL 8.4.9，总行数约 55.3 万），抽测查询 177 ms 内；因此时间桶查询按方案直接实施，无需建索引授权。实施后实测：趋势 `1h`/分钟桶 228 ms、**缓存命中 3–10 ms**、7 天/小时桶 160 ms、`30d`/天桶 280–510 ms（受缓存与 MySQL 缓冲影响），TOP 排行 77 ms，心跳历史 4 ms。
- 文档：`API.md`（§2 总表 22 → 25 + §4.6/§4.7/§6.7 新章节）、`ARCHITECTURE.md`（§4.1/§4.2 页面链路与轮询节奏、§7 聚合与心跳历史查询路径、§8 状态表、§12 限制 10–13 条）、`README.md`（§1/§2 能力、§3 代码树、§6.1 测试与自检工具、§8 排障）、`DECISIONS.md`（ADR-016/ADR-017）、`TODO.md`（概览 2s `COUNT(*)` 债标注部分缓解 + 新增两条）。

### Removed — 2026-10-13（移除 SSE，实时通道统一为 WebSocket）

- **整体移除 SSE 通道**：删除 `GET /api/v1/logs/stream` 路由、`event_queue`（进程内 `asyncio.Queue`）、
  `sse_clients` 计数、`sse-starlette` 依赖，以及**只为 SSE 存在的** `_summary_from_row`/`_summary_from_raw`
  与 `persist_logs()` 返回的 `summaries`。
  - 原因：该通道已无内置消费方（前端自 ADR-012 起就不再订阅），却带着两个真实缺陷——所有订阅者
    **竞争同一个队列**（各拿到不同事件）、队列满时**反向阻塞采集**；同时它让「实时能力」存在两套语义。
  - 采集接口不再向任何通道推送逐条日志事件；页面靠定时 REST 刷新（概览 2 秒、日志/存证页 15 秒）。
- **`/health` 结构变化（破坏性）**：`realtime` 现在只有 `ws_clients`，`sse_clients` 字段被移除。
  外部监控若读取该字段需同步修改。
- **测试**：`tests/test_main_contracts.py` 的接口总表断言 23 → 22；原先用「SSE 事件记录器」验证解析结果的
  用例改为断言**真正准备写入数据库的行**（`_persist_rows` 的入参：表名、category、时间归一化、account），
  断言强度不降；新增 `test_sse_endpoint_is_removed` 守卫（该路径不再是接口、`/health` 无 `sse_clients`）。
- **文档**：`docs/API.md`（§2 总表、§3 `/health`、§5 标题与实时通道说明、删除 §5.2 SSE 全节）、
  `docs/ARCHITECTURE.md`（概览图、组件表、生命周期、采集时序图、§5.3 改写为「实时通道统一为 WebSocket」
  + SSE 历史记录、状态表、故障表、限制清单）、`docs/DECISIONS.md`（新增 ADR-015）、
  `docs/README.md`、`docs/TODO.md`（删除已失效的 SSE 单队列技术债）、`backend/README.md`、
  `AGENTS.md`、`AGENT_RULES.md`。
- 顺带修正 `docs/ARCHITECTURE.md` 概览图里两处过期内容：仍画着 `jsDelivr CDN` 依赖（ADR-011 早已改为
  自托管）、`log_fusion` 只画了 `client_status`（现含 `archive_log`）。
- 无数据库结构变化；接口从 23 条减少为 22 条；测试总数 166 → **168**（新增 `test_sse_endpoint_is_removed`
  守卫与 `test_websocket_sends_snapshot_on_connect` 契约用例——后者此前完全缺失）。

### Changed — 2026-10-13（概览统计改为 2 秒刷新）

- 运行概览的日志数量由每 10 秒刷新改为**每 2 秒**刷新（`app.js` 的 `summaryTimer`）；「日志总量」卡片下方的文案
  由「每 10 秒刷新」直接改为 **「实时刷新」**（不再暴露轮询间隔——实际机制仍是每 2 秒轮询 REST，见下条与
  `docs/ARCHITECTURE.md` §4.1）。
- 「实时通道」指标在 WebSocket 已连接时只显示「WebSocket 已连接」，去掉原来的「（5 秒推送）」后缀
  （推送节奏没变，只是不再写进指标文字）；断开时仍显示「WebSocket 断开，改用 5 秒轮询」。
- 因为 `/api/v1/logs/summary` 是 12 张表的 `COUNT(*)`，**实测单次 286–497 ms**，所以 2 秒刷新加了两条保护：
  - **不叠加**：`summaryBusy` 保证同一时刻只有一条请求在飞，慢响应不会被下一次定时器堆成队列
    （这正是历史上「页面一直转圈」的成因之一）；
  - **只在本页轮询**：仅当概览页可见时由定时器触发（在日志/存证页看不到这些数字，不必付这份数据库开销）；
  - 另外统计结果未变化时不再重画环形图，避免每 2 秒白跑一次 `setOption`。
- `app.js` 版本参数 → `?v=20261013-2`，强制浏览器刷新旧缓存。
- 文档同步：`docs/ARCHITECTURE.md` §4.1（刷新节奏与两条保护）、`docs/DECISIONS.md` ADR-012 追加
  2026-10-13 调整说明（含「更快的刷新 = 持续数据库开销」的提醒）、`docs/TODO.md` 新增一条
  「概览每 2 秒对 12 张表 `COUNT(*)`」的技术债（旧契约明确建议改为服务端缓存/定时聚合）。
- 顺带修正文档一处旧描述：`docs/ARCHITECTURE.md` §4.2 仍写「存证页表不可用时返回空数据」，
  实际自 2026-10-12 起返回 **503**。
- 无接口行为变化（`/api/v1/logs/summary` 契约不变，只是被更频繁地调用）。

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
