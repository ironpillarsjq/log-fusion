"""前端图表接线契约：模板里的每个图表容器都必须真的被 app.js 绘制。

对应 `docs/PLAN-frontend-charts.md` §5。历史教训：模板先加了 10 个图表容器、
`app.js` 却还没实现对应绘制函数——页面能正常打开，但每张图都是空白，
而且不会有任何报错。所以这里把「模板容器 ⟷ app.js 绘制函数」钉成成对断言，
并守住二级导航的深链、轮询节奏与图表实例释放。
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_JS = REPO_ROOT / "frontend" / "app.js"
DOCS_README = REPO_ROOT / "docs" / "README.md"
TOOLS_DIR = REPO_ROOT / "tools"


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(scope="module")
def dashboard_html():
    return (REPO_ROOT / "backend" / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js():
    return APP_JS.read_text(encoding="utf-8")


def _chart_container_ids(html: str) -> set[str]:
    """模板里所有承载 ECharts 的容器 id（class 为 chart / chart-sm / chart-md）。"""
    return set(re.findall(r'<div id="([^"]+)" class="chart(?:-sm|-md)?">', html))


def test_every_chart_container_is_drawn_by_app_js(dashboard_html, app_js):
    """模板里的每个图表容器都必须被绘制层引用（`paint('<id>'` 或 `getChart('<id>')`）。

    历史教训：模板先加了图表容器、app.js 却没跟上，页面能打开但每张图都是空白，
    而且不会有任何报错——所以这条断言是「半成品检测器」。
    """
    ids = _chart_container_ids(dashboard_html)
    assert len(ids) >= 10, f"模板里的图表容器少于预期（实际 {sorted(ids)}）"
    missing = [chart_id for chart_id in sorted(ids)
               if f"paint('{chart_id}'" not in app_js and f"getChart('{chart_id}')" not in app_js]
    assert not missing, f"以下图表容器在 app.js 里没有绘制代码：{missing}"


def test_expected_charts_exist_for_each_page(dashboard_html):
    """三个页面的图表清单（漏一个就是「需求少做了一张图」，必须显式失败）。"""
    ids = _chart_container_ids(dashboard_html)
    expected = {
        "overview": {"trend-chart", "platform-gauge", "category-chart"},
        "heartbeat": {"delay-chart", "status-donut"},
        "log-stats": {"stats-trend-chart", "stats-platform-chart", "stats-heatmap",
                      "stats-hosts-chart", "stats-categories-chart"},
    }
    for page, charts in expected.items():
        assert charts <= ids, f"{page} 页缺少图表容器：{sorted(charts - ids)}"


def test_online_rate_gauge_is_removed(dashboard_html, app_js):
    """在线率仪表已按需求移除（2026-10-13）：模板与绘制代码里都不应再出现。

    在线率本身仍作为「在线客户端」指标卡的 hint 展示（文案在 app.js 里拼），
    被移除的只是那张 gauge 图。
    """
    assert "online-gauge" not in dashboard_html
    assert "drawOnlineGauge" not in app_js
    assert "在线率" in app_js, "指标卡上的在线率文案不该被一起删掉"


def test_overview_shows_hash_evidence_panel_instead_of_system_status(dashboard_html, app_js):
    """概览的「系统状态」清单已按需求移除，换成「哈希存证」面板（2026-10-13）。

    连接状态改由左侧「服务连接」卡片承担，所以概览里那三条硬编码的「已连接」
    （Linux 数据库 / Windows 数据库 / WebSocket 实时通道）都不该再出现——
    它们本来就只是写死的徽标，跟真实连接状态无关。
    """
    assert "哈希存证" in dashboard_html
    assert "系统状态" not in dashboard_html
    for stale in ("Linux 数据库", "Windows 数据库", "WebSocket 实时通道"):
        assert stale not in dashboard_html, f"概览里仍残留写死的状态行：{stale}"
    # 面板必须有真实数据绑定（数字、占比条、状态胶囊）与入口按钮
    assert "metricValue('hashMetrics','存证总数')" in dashboard_html
    assert "hashBar()" in dashboard_html and "hashPillText()" in dashboard_html
    assert "hashTiles()" in dashboard_html
    assert "loadEvidenceSummary" in app_js and "/api/v1/evidence/summary" in app_js


def test_hash_panel_never_shows_zero_when_query_fails(app_js):
    """存证库查询失败时接口返回 503：面板必须显示「不可用」，绝不能显示成 0 条。

    这是本项目反复强调的口径——「库连不上」和「确实没有存证」必须可区分。
    """
    assert "存证库查询失败" in app_js
    assert "这不代表没有存证" in app_js
    assert re.search(r"this\.hashMetrics=\[\]", app_js), "失败时要清空指标卡，否则会显示 0"
    assert re.search(r"this\.hashStatuses=null", app_js), "失败时不能留下上一次的状态计数"
    assert "metricValue" in app_js and "return m?m.value:'—'" in app_js, "取不到指标时应显示 —"


def test_sidebar_has_connection_card(dashboard_html, app_js):
    """侧栏「服务连接」卡片：数据库行来自 HTMX 片段，实时通道行来自 Alpine 本地状态。"""
    assert 'class="conn-card"' in dashboard_html and 'class="conn-inner"' in dashboard_html
    assert 'hx-get="/partials/db-status"' in dashboard_html
    assert 'hx-trigger="load, every 30s"' in dashboard_html
    # 实时通道那一行必须是本地状态驱动（WS 状态只有浏览器自己知道）
    assert "wsTone()" in dashboard_html and "wsLabel()" in dashboard_html and "wsHint()" in dashboard_html
    for method in ("wsTone()", "wsLabel()", "wsHint()"):
        assert method in app_js, f"app.js 缺少侧栏连接卡需要的方法 {method}"
    # 样式必须真的定义了这套卡片（否则只是一串裸文本）
    for rule in (".conn-inner{", ".conn-dot.ok{", ".conn-state.bad{", ".conn-foot{"):
        assert rule in dashboard_html, f"缺少样式 {rule}"


def test_latency_rank_chart_is_removed(dashboard_html, app_js):
    """「客户端延迟排行」已按需求移除（2026-10-13）：模板与绘制代码里都不应再出现。

    排行信息仍在下方的客户端列表里（含「延迟」列），被移除的只是那张条形图。
    """
    assert "latency-rank" not in dashboard_html
    assert "drawLatencyRank" not in app_js
    assert "客户端延迟排行" not in dashboard_html
    assert "客户端列表" in dashboard_html, "客户端列表不该被一起删掉"


def test_all_charts_go_through_paint_helper(app_js):
    """所有图表必须走 paint()。它负责三件事，缺一个就会出问题：

    ① 数据没变就不 setOption——WebSocket 每 5 秒推一次快照（多数轮次数据完全相同），
       无脑 setOption 会让 ECharts 重播整张图的入场动画，看上去就是「一直在闪」；
    ② 只有数据变时走合并更新；③ 只有 series 结构变时才 replaceMerge。
    """
    assert re.search(r"paint\(id,\s*option\)", app_js), "缺少统一绘制出口 paint(id, option)"
    assert "paintKeys" in app_js and "painted" in app_js
    # 绕过 paint 直接 setOption 就等于又回到「每 5 秒闪一次」，因此只允许 paint 内部出现一次
    assert len(re.findall(r"\.setOption\(", app_js)) == 1, "绘制函数必须走 paint()，不要直接 setOption"
    assert "animationDurationUpdate" in app_js, "合并更新要显式用较短的更新动画，避免视觉跳变"


def test_offline_clients_are_excluded_from_delay_charts(app_js):
    """掉线的机器不展示心跳延迟：离线时「延迟」= 心跳年龄，只会一路涨到无意义的大值。

    延迟趋势必须先按状态过滤，且平均线只能由未掉线的客户端计算。
    """
    assert "clientStatus" in app_js
    assert re.search(r"filter\(c=>this\.clientStatus\(c\)!=='OFFLINE'\)", app_js), "延迟图必须过滤离线客户端"
    assert app_js.count("!=='OFFLINE'") >= 2, "延迟趋势的取数与绘图两处都要过滤离线客户端"
    # 平均线若直接用接口的 avgDelay，会把离线客户端的陈旧延迟算进去，与图里的曲线对不上；
    # 必须由图中（未掉线）的曲线现算。这里只禁止「读取该字段」，注释里提到它不算违规。
    assert not re.search(r"\.\s*avgDelay", app_js), "平均延迟必须按图中未掉线的曲线计算，不要读取接口的 avgDelay"
    assert re.search(r"const\s+average\s*=", app_js), "缺少按可见曲线计算的平均延迟逻辑"


def test_delay_chart_plots_raw_polyline(app_js):
    """心跳延迟曲线按**原始采样值**画折线，不做任何平滑（2026-10-13 需求：取消平滑效果）。

    背景：平滑版（EWMA + 居中滑动平均）曾把「4↔20 秒交替」的抖动从 4.8 秒压到 1.0 秒，
    但平滑会掩盖单点尖峰，而尖峰正是这张图要看的东西，所以按需求改回原始折线。
    这条用例同时防止平滑代码被无意间「加回来」。
    """
    assert "smoothLine" not in app_js, "延迟曲线已按需求取消 EWMA 平滑"
    assert "movingAverage" not in app_js, "延迟曲线已按需求取消居中滑动平均"
    assert "smoothSeries" not in app_js, "两级平滑管线已整体移除"
    body = app_js.split("drawDelayChart(){")[1].split("// 状态分布甜甜圈")[0]
    assert "smooth:false" in body, "折线必须是直线段（smooth:false），不能是样条"
    assert "smoothMonotone" not in body, "折线不需要样条单调约束"
    assert body.count("smooth:false") >= 2, "平均线与客户端线都要是折线"
    assert "data:line" in body, "客户端曲线直接使用原始采样点"
    # 平滑值仍可从接口读到（avgDelay 是服务端算的），但图上画的必须是采样值本身
    assert not re.search(r"\.\s*avgDelay", body), "延迟曲线不读取接口的 avgDelay（那是全客户端均值）"


def test_delay_chart_matches_overview_trend_style(app_js):
    """心跳延迟趋势要与概览「接入趋势」同一套视觉，并且不能抖（2026-10-13 需求）。

    两个「不抖」的关键点都在选项里，容易被后来的改动破坏，所以钉住：
    ① 主序列（平均延迟）是渐变面积曲线，客户端细线退到背景；
    ② 纵轴锚定 0 与「凑整上限」——自动缩放会把 3~4 秒的小波动撑满整个高度，看起来就是剧烈震荡。
    """
    body = app_js.split("drawDelayChart(){")[1].split("// 状态分布甜甜圈")[0]
    assert "areaGradient(" in body, "平均延迟必须是渐变面积曲线（与概览趋势一致）"
    assert "this.tooltipAxis('秒')" in body, "轴 tooltip 要复用统一的十字准线样式"
    assert re.search(r"yAxis:\{[^}]*min:0[^}]*max:axisMax", body), "纵轴必须锚定 min:0 与凑整上限"
    assert "niceDelayMax" in app_js, "缺少纵轴上限的凑整逻辑"
    assert re.search(r"lineStyle:\{width:1,opacity:0\.\d+\}", body), "客户端细线要退到背景（细 + 半透明）"
    assert "markLine" in body, "延迟超过阈值时应给出 10 秒参考线"


def test_chart_heights_are_defined_in_styles(dashboard_html):
    """图表容器没有高度时 ECharts 画出来就是 0 高（页面看着像没加载）。"""
    for rule in (".chart{height:300px}", ".chart-sm{height:240px}", ".chart-md{height:300px}"):
        assert rule in dashboard_html, f"样式缺少 {rule}"


def test_charts_are_disposed_on_pagehide(app_js):
    """图表实例必须随页面卸载释放：否则刷新/切页会不断泄漏 ECharts 实例。"""
    assert "disposeCharts" in app_js
    assert "pagehide" in app_js
    assert re.search(r"pagehide[^\n]*disposeCharts", app_js), "pagehide 处理里必须释放图表实例"


def test_charts_skip_hidden_containers(app_js):
    """x-show 隐藏的区块宽高为 0，绘制前必须挡掉（否则画出 0×0 的空白图）。"""
    assert re.search(r"getChart\(id\)\s*\{[^}]*clientWidth", app_js), "getChart 必须检查容器可见性"


def test_chart_polling_targets_new_aggregate_endpoints(app_js):
    """三张实时图各自的数据源必须真的是新的聚合接口（参数也要对）。"""
    assert "'/api/v1/logs/trend?range=1h&bucket=minute'" in app_js, "概览趋势必须取 1 小时分钟桶"
    assert "/api/v1/monitor/history?minutes=15" in app_js, "心跳延迟趋势必须取 15 分钟历史窗口"
    assert "/api/v1/logs/stats?range=" in app_js, "统计页排行必须取窗口内 TOP 统计"


def test_frontend_handles_utc_buckets_with_local_time(app_js):
    """后端只出 UTC 桶键，前端必须经过 Date 转换成本地时间，不能直接截字符串当横轴。"""
    assert "new Date(iso)" in app_js or "new Date(" in app_js
    assert "fmtClock" in app_js
    # 直接切 ISO 字符串（如 bucket.slice(11,16)）在 UTC 与本地时区间会整体偏 8 小时
    assert not re.search(r"bucket\.(?:slice|substring|substr)\(", app_js), "不得直接截取 UTC 字符串当时刻标签"


def test_secondary_navigation_and_deep_links(dashboard_html, app_js):
    """融合日志管理是唯一的二级菜单，且五个页面都要能从 URL 深链直达。"""
    assert "navLogsOpen" in dashboard_html and "navLogsOpen" in app_js
    assert 'class="nav-sub"' in dashboard_html
    for page in ("overview", "heartbeat", "logs", "log-stats", "evidence"):
        assert f"page==='{page}'" in dashboard_html, f"模板缺少 {page} 的区块"
        assert f"'{page}'" in app_js, f"app.js 不认识页面标识 {page}"
    assert "replaceState" in app_js, "切页必须同步 URL hash（刷新后停在原页面）"
    assert "pageFromHash" in app_js, "启动时必须从 hash 恢复页面"


def test_log_stats_page_has_filters(dashboard_html):
    """统计子页的筛选器（平台 + 时间范围 + 手动刷新）不能少。"""
    assert 'x-model="statsPlatform"' in dashboard_html
    assert 'x-model="statsRange"' in dashboard_html
    for option in ('value="24h"', 'value="7d"', 'value="30d"'):
        assert option in dashboard_html, f"统计页缺少时间范围 {option}"


# --- 存证中心（P0：功能分区 + 时间/平台筛选）-----------------------------------

def test_evidence_center_is_split_into_tabs(dashboard_html, app_js):
    """存证页从「一个长面板」改为按职责分区的存证中心。

    归档（会删除数据）不能和日常查询混在同一个面板里，否则误触成本太高；
    算法与策略也需要独立位置，避免把安全边界藏在表格下方的小字里。
    """
    assert "evidenceTabs()" in dashboard_html and "evidenceTabs()" in app_js
    for tab in ("overview", "ledger", "archive", "policy"):
        assert f"evidenceTab==='{tab}'" in dashboard_html, f"存证中心缺少分区 {tab}"
    for label in ("存证总览", "存证台账", "归档管理", "算法与策略"):
        assert label in app_js, f"存证中心缺少分区名称 {label}"


def test_evidence_center_has_platform_and_date_filters(dashboard_html, app_js):
    """平台 + 入库时间范围是审计排查的基本维度，缺一不可。

    注意平台取值是小写 `linux`/`windows`，与后端 `platform = :platform` 的
    存储口径一致；写成大写会在 MySQL 默认排序规则下碰巧命中，但依赖隐式行为。
    """
    assert 'x-model="evidencePlatform"' in dashboard_html
    assert 'x-model="evidenceStart"' in dashboard_html
    assert 'x-model="evidenceEnd"' in dashboard_html
    assert 'value="linux"' in dashboard_html and 'value="windows"' in dashboard_html
    for param in ("platform", "start", "end"):
        assert f"q.set('{param}'" in app_js, f"app.js 未把 {param} 传给列表接口"


def test_evidence_export_uses_current_filters(dashboard_html, app_js):
    """导出必须复用当前筛选条件，否则会导出与页面所见不一致的数据。"""
    assert "exportEvidence()" in dashboard_html
    assert "evidenceExportUrl()" in app_js
    # 导出参数与列表参数必须来自同一组状态，不能再有写死的导出链接
    assert 'href="/api/v1/evidence/export"' not in dashboard_html
    for param in ("verify", "keyword", "platform", "start", "end"):
        assert f"q.set('{param}'" in app_js


def test_evidence_filters_can_be_reset(dashboard_html, app_js):
    """筛选器需要重置入口，否则多个条件叠加后用户只能逐项手工清空。"""
    assert "resetEvidenceFilters()" in dashboard_html
    assert "resetEvidenceFilters()" in app_js
    assert "evidenceStart=''" in app_js and "evidenceEnd=''" in app_js


def test_chart_self_check_tools_are_documented():
    """两个不依赖浏览器的图表自检工具必须留在仓库里，并在文档里可被发现。"""
    for tool in ("chart-layout-check.cjs", "chart-render-check.cjs"):
        assert (TOOLS_DIR / tool).is_file(), f"缺少自检工具 {tool}"
        assert tool in DOCS_README.read_text(encoding="utf-8"), f"docs/README.md 未登记 {tool}"
