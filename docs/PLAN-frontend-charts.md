# 前端图表增强与二级导航改造方案

> 状态：**已实施（Phase 0–6 全部完成，2026-10-13）** · 编制日期：2026-10-13 · 事实源：`docs/` + `backend/app/main.py`/`metrics.py`、`frontend/app.js`、`backend/app/templates/dashboard.html`
> 本文是**实施方案与验收依据**；实施结果与偏差记录见 §0，现行行为以 `docs/`（API/ARCHITECTURE/CHANGELOG）与代码为准。

## 0. 实施记录（2026-10-13）

**Phase 0（只读核验索引）结论**：12 张表的时间列都已有索引（`idx_event_time` / `idx_time_created`），MySQL 8.4.9，总行数约 55.3 万；抽测查询 177 ms 内、数据新鲜（最大 `event_time` 为当天）。因此时间桶查询按方案直接实施，**无需建索引授权**。实施后真实数据实测：趋势 `1h`/分钟桶 228 ms、**缓存命中 3–10 ms**、7 天/小时桶 160 ms、`30d`/天桶 280–510 ms、TOP 排行 77 ms、心跳历史 4 ms。

**各 Phase 产出**：

| Phase | 结果 |
|---|---|
| 1 | `backend/app/metrics.py`（趋势/排行 + TTL 缓存、心跳历史环形缓冲）；`main.py` 新增 3 条路由（22 → 25）；`tests/test_metrics.py`；`docs/API.md` §2/§4.6/§4.7/§6.7 |
| 2 | `dashboard.html` 二级导航 + `log-stats` 区块；`app.js` 的 `go()`/`pageFromHash()`/hash 深链、图表注册表、统一轮询节奏 |
| 3 | 概览：接入趋势堆叠面积图、平台占比半环仪表、指标卡数字滚动 |
| 4 | 心跳页：延迟趋势、在线率仪表、状态分布甜甜圈、延迟排行（WS 快照与 REST 降级共用 `applyCounts()`）——后两张图已按需求移除，见下「实施后按需求调整」 |
| 5 | 统计子页：趋势、平台占比、7×24 热力图、TOP 主机/类别、平台与时间范围筛选器 |
| 6 | `app.js?v=20261013-4`；`tools/chart-render-check.cjs` 新增；`docs/` 全部回写；`tests/test_frontend_contracts.py` 接线契约 |

**与本文档的既定差异（以代码与 `API.md` 为准）**：

1. 心跳历史字段是 `avgDelay`（不是本文档草稿里的 `avgLatency`），且每点还带 `total`；响应急额外返回 `windowMinutes`、`samples`，供页面显示空态原因。
2. `topHosts[]` 实际只有 `{host, count}`：两平台同名主机合并计数，无法归属单一平台，因此按方案的 `platform` 字段未能提供。
3. 趋势聚合实现为「**同一引擎一条 `UNION ALL` 查询**」（Linux 7 表 / Windows 5 表各一条），而不是逐表查询后在 Python 合并——把 12 次往返压成 2 次。
4. `range` 与 `bucket` 组合出的点数超过 2000（如 `30d`+`minute`）在**查库前**返回 400，避免空桶填充把响应放大。
5. `GET /logs/stats` 按方案 §3.2 的备注补上了 `platform` 参数（统计页筛选器会带上）。
6. 统计子页默认时间范围取 `24h`（页面筛选器第一个选项），热力图固定使用 `7d`/小时桶的数据。
7. 新增了方案里只列为「可选 Phase 6」的图表自检工具 `tools/chart-render-check.cjs`（详见 §7）。

**实施后按需求调整（2026-10-13，代码与 `CHANGELOG.md` 为准）**：

1. **移除心跳页的「在线率」仪表图**：在线率就是「在线客户端 / 总数」，已在该指标卡的 hint 里显示，独立画一张 gauge 属于重复信息。心跳页图表由 4 张减为 3 张，布局改为「延迟趋势(8) + 状态分布(4)」与「延迟排行(12)」两行。
2. **掉线的客户端不再展示心跳延迟**：离线时「延迟」等于心跳年龄（`heartbeatDelaySec`），会随掉线时间一路增长，画出来只是噪音。延迟趋势与延迟排行都先按客户端状态过滤（只保留 ONLINE/DELAYED），并在图下注明排除了几个离线客户端；全部离线时显示「暂无未掉线客户端」空态。状态分布甜甜圈仍完整展示离线数量（那是状态，不是延迟）。
3. **延迟趋势的「平均延迟」改为按图中（未掉线）曲线现算**：原实现直接用接口的 `avgDelay`，它包含离线客户端的陈旧延迟，均值会浮在所有曲线之上，与图里看到的对不上。接口字段保持不变（仍是全客户端均值），只调整展示口径。
4. **修掉「WS 推送后整张图从 0 重新长出来」的闪烁**：新增统一绘制出口 `paint(id, option)`——数据+布局没变完全不重画；只有数据变则合并更新（`animationDurationUpdate` 280ms，只过渡数值）；只有 series 结构变（客户端增减、平台筛选切换）才 `replaceMerge`。`resize()` 只在实例尺寸与容器不一致时调用，窗口缩放时也不再 resize 隐藏区块。
5. **移除心跳页的「客户端延迟排行」条形图**：心跳页图表由 3 张再减为 2 张，布局只剩「延迟趋势(8) + 状态分布(4)」一行；排行信息仍在下方「客户端列表」的「延迟」列。模板容器 `latency-rank`、绘制函数 `drawLatencyRank()` 与相关用例/文档已一并删除（`tests/test_frontend_contracts.py` 加了守卫断言防止删一半留下半成品）。
6. **延迟趋势曲线做指数平滑**：5 秒一个采样点的抖动会让曲线在相邻点之间大跳，`drawDelayChart()` 对每条曲线先过一次 `smoothLine()`（EWMA，α=0.3）再交给 ECharts（`smooth: 0.7`）；平滑只在绘制层做，接口返回的仍是原始采样值，断点（无采样）仍输出 `null` 保持断线不连。


## 1. 背景与目标

现状：运行概览只有 1 个 ECharts 环形图 + 4 张指标卡 + 系统状态列表；心跳监控只有指标卡 + 表格（无任何图表）；左侧导航是 4 个平铺一级项；全站没有任何时间序列数据接口——所有接口都只返回「当前时刻快照」。

目标（三条，对应本次需求）：

1. **运行概览**升级为实时动态仪表盘（趋势图 + 平台占比仪表，数据随时间滚动）。
2. **客户端心跳监控**加入图表（延迟趋势、在线率仪表、状态分布、延迟排行）。
3. **左侧导航改二级菜单**：仅「融合日志管理」拆出子菜单「日志检索 / 日志统计图表」，新增日志统计图表子页面（趋势图、24h×星期热力图、TOP 主机/类别排行）。

已确认的三个关键决策：

| 决策点 | 结论 |
|---|---|
| 图表数据来源 | **全部走新后端聚合接口（含服务端缓存）**，前端不维护历史数据缓冲；现有 summary/monitor 接口继续承担「当前快照」类指标 |
| 二级导航范围 | **仅融合日志管理有子菜单**，概览/心跳/存证保持一级项 |
| 统计子页图表 | 接入时间趋势图、24 小时×星期热力图、TOP 主机 / TOP 类别排行（**不做**结果成败分布） |

## 2. 硬性约束（方案必须守住）

1. **不加任何新依赖**：只用已自托管的 ECharts 5.5.1（`frontend/vendor/`），不引 CDN、不加 npm 构建（ADR-002/ADR-011）。
2. **不新增长连接**：浏览器同源 6 条 HTTP/1.1 额度不变，仍只保留 1 条 `/ws/client-monitor`（ADR-012）；新图表一律靠现有轮询节奏取数。
3. **ECharts 三条铁律**（现有 `drawChart` 注释已沉淀，须推广到所有新图）：① 实例复用，禁止重复 `echarts.init`；② `x-show` 隐藏容器宽高为 0 时必须跳过绘制；③ `resize` 150ms 防抖重画。
4. **不加重数据库压力**：概览现有 2 秒 `summary` 已是 12 张表 `COUNT(*)`（实测 286–497 ms，`TODO.md` 已立项缓存）。**新接口必须带服务端 TTL 缓存**，且刷新频率不得比 summary 更密（趋势图 10s、统计页 30s 起步）。
5. **必须单 worker**（既有约束）：新缓存与心跳历史缓冲都在进程内，与现有架构一致，不引入跨进程状态。
6. 改动后：`app.js?v=` 版本参数递增；跑 `python -m pytest`；若动环形图布局须跑 `node tools/chart-layout-check.cjs`（AGENTS.md 要求）。

## 3. 后端方案：3 个新聚合接口

新增 3 条路由，接口总数 **22 → 25**（`tests/test_main_contracts.py:78` 的断言必须同步改为 25）。

### 3.1 `GET /api/v1/logs/trend` — 时间桶接入趋势

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `range` | `1h` \| `24h` \| `7d` \| `30d` | `24h` | 时间窗口，按 `event_time`/`time_created` 过滤 |
| `bucket` | `minute` \| `hour` \| `day` | 随 range 定（1h→minute，24h→hour，7d/30d→day） | 桶粒度 |
| `platform` | `Linux` \| `Windows` \| 省略=全部 | 全部 | 只查对应库 |

响应（`data`）：

```json
{"bucket":"hour","range":"24h","generatedAt":"2026-10-13T06:00:00Z","cached":true,"ageMs":4200,
 "points":[{"bucket":"2026-10-13T05:00:00","linux":120,"windows":300,"total":420}]}
```

- 空桶补 0，前端直接喂 `setOption`。
- **时间一律 UTC ISO 字符串**（与现有接口口径一致，DB 内即 UTC naive）；**时区转换由前端负责**（见 §5.5），否则热力图/趋势会错 8 小时。
- 聚合方式：沿用 `summary` 的「逐表查询、Python 合并」模式——Linux 7 表按 `event_time`、Windows 5 表按 `time_created`（Windows 表**没有** `event_time` 列，见 `DATABASE.md` §5）。
- **缓存**：进程内 TTL dict，key=`(range,bucket,platform)`；`minute/1h` TTL 5s，`24h` TTL 15s，`7d/30d` TTL 60s。响应必带 `generatedAt`/`cached`/`ageMs`（落实 `TODO.md`「缓存时间戳可在响应中看到」的验收口径）。
- 错误：查询失败抛未捕获异常 → HTTP 500（与 `/logs/summary` 现状一致，`api()` 抛错后页面走 `warn()` 提示条）。

### 3.2 `GET /api/v1/logs/stats` — 排行聚合

| 参数 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `range` | `24h` \| `7d` \| `30d` | `7d` | — |
| `limit` | integer | `10` | 1～50 |
| `platform` | `Linux` \| `Windows` \| 省略 | 全部 | 供统计页的平台筛选器使用（实现时补充） |

响应（`data`）：

```json
{"generatedAt":"...","cached":false,"ageMs":0,
 "topCategories":[{"key":"authentication_session","label":"认证会话","platform":"Linux","count":8123}],
 "topHosts":[{"host":"server-a","platform":"Linux","count":1502}]}
```

- `topCategories`：12 张表在窗口内 `COUNT(*)` 排序取前 N（实现时可复用 summary 缓存，但 summary 是全表口径，本接口按窗口统计，二者语义不同，不混用）。
- `topHosts`：**必须按表适配缺列**——Linux 只有 `authentication_session`、`account_security_change`、`system_service_audit_lifecycle` 三张表有 `hostname`（`DATABASE.md` §4.3），其余 4 表跳过；Windows 5 表统一取 `computer`。合并后按 host 聚合取前 N。
- 缓存 TTL：60s。

### 3.3 `GET /api/v1/monitor/history` — 心跳历史序列

| 参数 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `minutes` | integer | `15` | 1～60 |

响应（`data`）：

```json
{"intervalSec":5,"generatedAt":"...",
 "points":[{"t":"2026-10-13T06:01:00Z","online":4,"delayed":1,"offline":1,"avgLatency":12}],
 "clients":[{"clientId":"sim-linux-001","status":"ONLINE","points":[["2026-10-13T06:01:00Z",8]]}]}
```

- **数据源：进程内环形缓冲，不查库、不建新表**。在现有 `_status_watchdog`（每 5 秒一轮，ADR-009）里顺手 append 一个采样点，容量 720 点 = 1 小时，内存开销可忽略。
- `points` 为全局计数序列（online/delayed/offline + 平均延迟）；`clients` 为每客户端延迟序列（只含窗口内出现过的客户端，延迟缺失记 `null`）。
- 已知限制（须写进文档）：**进程重启后历史清零**，页面显示空态；将来若要跨重启保留，再升级为心跳历史表（本次不做，避免 DDL 变更）。
- 该接口同时天然满足「实时」：数据每 5s 滚动一次，与 WS 快照同节拍。

### 3.4 索引前置核验（实施 Phase 0）

**结论（2026-10-13 已执行）**：12 张表的时间列都已有 `idx_event_time` / `idx_time_created`，因此按「有索引」分支直接实施——**不需要建索引，也不需要额外授权**。细节与实测耗时见 §0。

`2026-10-10` 的只读核验**只核了列名，没核索引**。时间桶 `GROUP BY` 若 `event_time`/`time_created` 无索引，30d 窗口会全表扫描。实施前先执行只读 `information_schema.STATISTICS` 核验（与既往核验方式一致，不执行任何 DDL/DML）：

- 有索引 → 直接按方案实施；
- 无索引 → 本方案降级为「靠 TTL 缓存 + 默认 24h 窗口」并把「给 `event_time`/`time_created` 建索引」作为**需单独授权的数据库变更**登记进 `TODO.md`（不擅自改线上表）。

## 4. 导航信息架构（二级菜单）

```text
平台导航
├─ ⌂ 运行概览              page='overview'
├─ ◉ 客户端心跳监控         page='heartbeat'
├─ ▤ 融合日志管理 ▸         page∈{logs, log-stats} 时父项高亮，可展开/折叠
│    ├─ 日志检索            page='logs'      （原页面，不变）
│    └─ 日志统计图表        page='log-stats' （新增子页）
├─ ✓ 完整性存证             page='evidence'
└─ [服务连接状态卡]         （HTMX 片段：4 个库 + 浏览器侧实时通道，2026-10-13 改版）
```

实现要点：

1. Alpine 新增状态 `navLogsOpen`（默认 false）、`page` 增加取值 `'log-stats'`。
2. 父项点击 = 切换展开/折叠（带 `x-transition` 与箭头 `▸→▾` 旋转）；子项点击 = 切页 + 懒加载数据（首次才请求，之后由页面定时器刷新）。
3. **active 继承**：`page` 为 `logs` 或 `log-stats` 时父项与对应子项同时高亮；子项用缩进 + 左侧竖线指示器区分层级。
4. **URL hash 同步**（新增，成本低收益大）：`#/overview` `#/heartbeat` `#/logs` `#/log-stats` `#/evidence`。切页时 `history.replaceState`，启动时解析 hash 恢复页面并自动展开对应子菜单——刷新不再回到概览页。
5. 侧栏高度与滚动：保持现有 `sidebar` 样式，二级列表用浅背景 `#f8faff` 区分。
6. 页面区块：新增 `<section x-show="page==='log-stats'" x-cloak>`，与现有四个 section 并列。

## 5. 图表设计

统一视觉规范（所有新图适用）：

- 色板沿用环形图现有 12 色数组（`#2f6bff #4cc38a #f2b134 …`），主色 `#2f6bff`。
- 面积图用 `echarts.graphic.LinearGradient` 渐变填充（28% → 透明）；网格线 `#eef2f8` 虚线；轴标签 11px `#728197`；分隔线 `#e4eaf3`。
- 动画 `duration:300, easing:'cubicOut'`；tooltip 轴触发 + 十字准线，数值 `toLocaleString('zh-CN')`。
- 空态统一 `title:{text:'暂无数据'}`（沿用现有模式）；加载中用 `chart.showLoading()`。
- 容器高度体系：新增 `.chart-sm{height:240px}` / `.chart-md{height:300px}`（现有 `.chart` 保留 300px 给环形图，避免破坏 `chart-layout-check.cjs` 的 CHART_HEIGHT 断言）。
- 响应式：沿用「≥560px / <560px」双布局分支模式，窄屏折图例、缩半径。

### 5.1 运行概览（增强）

| 行 | 图表 | 数据源 | 刷新 |
|---|---|---|---|
| Row1 | 4 张指标卡（保留，加数字滚动 count-up 动画，纯 JS 无依赖） | 现有 `/logs/summary` | 现有 2s |
| Row2 左 (col-xl-8) | **实时接入趋势**：Linux/Windows 双序列堆叠面积图，X 轴最近 60 分钟按分钟桶 | 新 `/logs/trend?range=1h&bucket=minute` | 新定时器 10s（`trendBusy` 防叠加，仅概览页可见时） |
| Row2 右 (col-xl-4) | **平台占比半环仪表**：Linux/Windows 百分比，中心显示总量 | 现有 summary `by_platform` | 现有 2s（签名不变不重画） |
| Row3 | 日志类别分布环形图 + 系统状态（**保持现状不动**） | 现有 summary | 现有 2s |

> 保留环形图布局原样，是为了让 `tools/chart-layout-check.cjs` 继续通过；本阶段不改 `drawChart` 的几何逻辑。

### 5.2 客户端心跳监控（新增）

| 行 | 图表 | 数据源 | 刷新 |
|---|---|---|---|
| Row1 | 4 张指标卡（保留；在线率在「在线客户端」的 hint 上） | WS 快照 / 现有 monitor 接口 | WS 每 5s |
| Row2 左 (col-xl-8) | **心跳延迟趋势折线图**：每客户端一条细线 + 未掉线客户端的平均粗线，滚动 15 分钟窗口 | 新 `/monitor/history?minutes=15` | 10s 轮询（仅心跳页可见时） |
| Row2 右 (col-xl-4) | **状态分布甜甜圈**：ONLINE/DELAYED/OFFLINE 三色 | 现有 monitor/summary（WS 快照即时更新） | WS 每 5s |
| ~~Row3 (col-xl-12)~~ | ~~**客户端延迟排行横向条形图**~~ **已于 2026-10-13 按需求移除**（排行信息在下方客户端列表的「延迟」列） | — | — |
| 其后 | 现有客户端列表表格（不变，可加状态筛选 tab，可选） | — | — |

> ~~Row2 右「在线率仪表盘 gauge」~~ **已于 2026-10-13 按需求移除**（在线率属于指标卡信息，不需要单独一张图）；
> ~~Row3「客户端延迟排行」~~ **已于 2026-10-13 按需求移除**（见 §0 实施后调整第 5 条）。
> 原 Row3 左的状态分布甜甜圈移到 Row2 右并改为 `chart-md`（与趋势图同高）。
> 延迟趋势**只展示未掉线（ONLINE/DELAYED）的客户端**，离线客户端不参与延迟计算。

- WS 断开降级为 5 秒 REST 轮询时，走同一 `applySnapshot()`，甜甜圈**自动降级继续更新**，无额外代码路径。
- 延迟趋势图依赖 history 接口，降级期间照常轮询（该接口本身不依赖 WS）。
- 可选增强（本次不做，登记 TODO）：客户端状态时间线热力图（client × 时间 的状态色带）。

### 5.3 新子页「日志统计图表」

页面结构：头部筛选器（平台 Linux/Windows/全部、时间范围 24h/7d/30d、↻ 手动刷新、自动刷新 badge）+ 三行图表；进入页面懒加载，之后 30s 定时刷新（仅本页可见时，`statsBusy` 防叠加）。

| 行 | 图表 | 数据源 |
|---|---|---|
| Row1 左 (col-xl-8) | **接入时间趋势**堆叠面积图（平台筛选决定单/双序列；24h→小时桶、7d→小时桶、30d→天桶） | `/logs/trend` |
| Row1 右 (col-xl-4) | **平台占比甜甜圈** | `/logs/trend` 窗口合计 或 summary |
| Row2 左 (col-xl-7) | **24 小时 × 星期热力图**：7 行（周一~周日）× 24 列，`visualMap` 从 `#e8f1ff` 到 `#142d70` | `/logs/trend?range=7d&bucket=hour` → **前端按本地时区**聚合出 7×24 矩阵 |
| Row2 右 (col-xl-5) | **TOP 主机排行**横向条形图 | `/logs/stats` 的 `topHosts` |
| Row3 (col-xl-12) | **TOP 日志类别排行**横向条形图（12 类别，按平台着色） | `/logs/stats` 的 `topCategories` |

- 热力图由前端从后端小时序列聚合，而不是再加第三个接口：**数据仍全部来自后端**，前端只做展示变换，且顺带正确处理本地时区（后端只出 UTC）。已知口径：7 天是滚动窗口，边缘格子样本略少，图下加注「基于最近 7×24 小时滚动窗口」。

### 5.4 前端工程化改造

- 新增图表注册表 `charts:{}` + `getChart(id)`（复用/损坏重建）+ `resizeAll()`（150ms 防抖重画所有可见图）+ `pagehide` 时 `dispose()` 全部实例（目前只关 WS，图表实例会随刷新泄漏，顺手修掉）。
- 所有新轮询统一「**页面可见 + busy 标志 + 8s 超时**」三件套（沿用 `summaryBusy`/`api()` 模式）。
- 定时器清单（改造后）：

| 定时器 | 间隔 | 触发条件 |
|---|---|---|
| summary | 2s | `page==='overview'` 且 `!summaryBusy`（不变） |
| trend（新） | 10s | `page==='overview'` 且 `!trendBusy` |
| 心跳 history（新） | 10s | `page==='heartbeat'` 且 `!historyBusy` |
| logs/evidence（现有 15s） | 15s | 对应页可见（不变） |
| log-stats（新） | 30s | `page==='log-stats'` 且 `!statsBusy` |
| WS 断线 5s 轮询（现有） | 5s | WS 未连接（不变） |

### 5.5 时区口径（必须写进代码注释与文档）

- 后端所有时间桶输出 **UTC ISO**；前端渲染前统一转本地时区（`new Date(iso).toLocaleString`），热力图列头用本地小时。
- 心跳 history 的 `t` 同为 UTC；`last_heartbeat` 等字段维持现有展示方式不变。

## 6. 实施分期

| Phase | 内容 | 产出 | 验收 |
|---|---|---|---|
| **0** | 只读核验 `event_time`/`time_created` 索引（information_schema，不执行 DDL） | 结论写入本方案/DATABASE 待确认清单 | 明确查询策略或登记建索引待办 |
| **1** | 后端 3 接口 + TTL 缓存 + 无库契约测试 | `main.py` +3 路由、`tests/` 新用例、`docs/API.md` §2 22→25 及新章节 | `python -m pytest` 全绿 |
| **2** | 二级导航 + `log-stats` 页面骨架 + hash 路由 | `dashboard.html` 导航重构、`app.js` 状态机 | 切页/刷新/深链正常，active 继承正确 |
| **3** | 概览图表（趋势 + 平台仪表 + count-up） | Row2 新增 | 趋势滚动、窄屏不错位 |
| **4** | 心跳页图表 | Row2/Row3 | WS 断开降级后甜甜圈仍更新（gauge/排行已按需求移除） |
| **5** | 统计子页 5 图表 + 筛选器 | 新 section | 三种时间范围切换正常、热力图时区正确 |
| **6** | 视觉打磨 + 自检 + 文档回写 | `app.js?v=` 递增、CHANGELOG/ADR/TODO 更新 | 见 §7 |

## 7. 验收与回归清单

- [x] `python -m pytest`：路由总数断言 22 → 25；新增 3 接口的参数校验/缓存/降级契约用例（继续不连库）。**共 199 个用例**；在受限沙箱里 178 个通过、`tests/test_archive.py` 的 13 个因 `tmp_path` 目录权限（pytest 用 `mode=0o700` 建目录）报 ERROR，与本次改动无关。
- [x] 图表自检：`node tools/chart-layout-check.cjs`（概览环形图几何/图例，4 种容器宽度）与新增的 `node tools/chart-render-check.cjs`（24 个用例：10 张图的渲染、空态文字、文字越界、热力图 168 格；`--live=http://127.0.0.1:8080` 追加 8 个真实数据用例）全部通过。
  > 2026-10-13 二次调整后（在线率仪表与延迟排行移除、延迟曲线加平滑）：该工具为 **21 个用例、9 张图**，并新增「重复推送不得重画」「延迟曲线必须做指数平滑」两组断言；`python -m pytest` 为 **204 个用例**。
- [x] 真实数据冒烟：起服务后实测 3 个新接口（含缓存命中、UTC 桶键、空窗口补 0），并用真实响应跑完 `--live` 的 8 个用例。
- [x] 文档同步：`docs/API.md`（总表 + §4.6/§4.7/§6.7）、`ARCHITECTURE.md`（§4.1/§4.2/§7/§8/§12）、`README.md`（§1/§2/§3/§6.1/§8）、`DECISIONS.md`（ADR-016/ADR-017）、`CHANGELOG.md`（Unreleased）、`TODO.md`（概览缓存债标注部分缓解 + 心跳历史未持久化 + 索引纳入 DDL）。
- [x] 无破坏性变更：现有 22 条接口行为不变，`/health` 结构不变。
- [ ] **待人工确认（浏览器里点一遍）**：多标签页仍只占 1 条 WebSocket、WS 断开后三张心跳图仍更新、窄屏 420px 下各图不错位、切页/深链/刷新后停在原页面、控制台无 `instance already initialized` 告警。沙箱内无法运行浏览器，这一步必须由人在真实浏览器完成。


## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| `event_time`/`time_created` 可能无索引 → 时间桶查询全表扫描 | Phase 0 只读核验；靠 TTL 缓存 + 默认 24h 窗口兜底；建索引作为需授权的独立事项登记 |
| 12 张表逐表 GROUP BY 比单次 COUNT 更重 | 缓存 TTL 分级（5s/15s/60s）；`platform` 参数可只查一半表 |
| 心跳历史进程内 → 重启丢失 | 文档明确标注 + 页面空态文案；将来要跨重启再升级为表（本次不做 DDL） |
| 时区错位（趋势/热力图偏 8 小时） | §5.5 强制约定 + 契约用例断言后端输出 UTC |
| 图表实例泄漏、隐藏容器画成 0×0 | 图表注册表 + `pagehide` dispose + clientWidth 守卫（现有铁律推广） |
| 路由数变化打破契约测试 | Phase 1 同步改 `test_main_contracts.py:78` 断言与 `API.md`（有 `test_doc_sync` 兜底） |
| 新定时器叠加请求 | 统一「可见 + busy + 8s 超时」三件套；总请求数不因图表翻倍 |
