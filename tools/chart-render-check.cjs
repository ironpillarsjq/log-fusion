#!/usr/bin/env node
/* 前端渲染自检（不依赖浏览器）。
 *
 * 背景：运行概览/心跳监控/日志统计/日志审计分类页共 11 张 ECharts 图（趋势面积图、半环仪表、
 * 状态甜甜圈、7×24 热力图等）。本机沙箱里无法运行无头浏览器，
 * 所以这里用「真实 app.js 的绘制函数 + 真实 frontend/vendor 的 ECharts 服务端渲染（SVG）」
 * 把每张图真正画一遍，断言：
 *   1. 绘制函数在容器可见时确实产出了 option；
 *   2. ECharts 能把该 option 渲染成 SVG 且不抛异常（series 类型、data 维度、visualMap 都要能被接受）；
 *   3. 画出的内容非空（不是一片空白画布）；
 *   4. 数据为空时必须画出「暂无」空态文字，而不是留一张空白图（历史上空数据图表看起来像加载失败）；
 *   5. 热力图矩阵恰好是 7×24 = 168 个格子（前端按本地时区聚合，格子数错说明聚合逻辑坏了）；
 *   6. 心跳延迟曲线按原始采样出折线（**不做平滑**），且纵轴锚定 0 与凑整上限
 *      （自动缩放的纵轴会把 3~4 秒的小波动撑满整个高度，看起来就是剧烈震荡）；
 *   7. 重复推送同一份数据不得重画、数值变化走合并更新（否则 WS 每 5 秒会让整页闪一次）；
 *   8. 概览「哈希存证」面板与侧栏「服务连接」卡的状态文案：存证查询失败必须显示「不可用」而不是 0。
 *
 * 用法（仓库根目录）：node tools/chart-render-check.cjs
 *   node tools/chart-render-check.cjs --live=http://127.0.0.1:8080   # 追加：用真实接口数据跑一遍
 * 退出码 0 = 全部通过，1 = 有断言失败。
 * 见 docs/PLAN-frontend-charts.md 与 docs/CHANGELOG.md。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ARGV = process.argv.slice(2);
const liveBase = (ARGV.find(a => a.startsWith('--live=')) || '').slice('--live='.length) || '';

const ROOT = path.resolve(__dirname, '..');
const ECHARTS = path.join(ROOT, 'frontend', 'vendor', 'echarts-5.5.1.min.js');
const APP_JS = path.join(ROOT, 'frontend', 'app.js');
const echartsLib = require(ECHARTS);

/* --- 用桩接管 echarts.init，把每种绘制方法的 option 接出来 --------------------- */

function makeHarness() {
  const elements = {};
  const captured = {};      // id -> 最后一次的 option
  const calls = {};         // id -> setOption 被调用的次数（用来证明「数据没变就不重画」）
  const settings = {};      // id -> 最后一次 setOption 的第二个参数（replaceMerge 与否）
  const stub = {
    graphic: echartsLib.graphic,
    init(el) {
      return {
        isDisposed: () => false,
        getDom: () => el,
        resize() {},
        setOption(option, opts) {
          captured[el.__id] = option;
          settings[el.__id] = opts;
          calls[el.__id] = (calls[el.__id] || 0) + 1;
        }
      };
    },
    getInstanceByDom: () => null
  };
  const sandbox = {
    window: { addEventListener() {}, echarts: stub },
    document: { getElementById: id => elements[id] || null },
    console: { log() {}, warn() {}, error() {} },
    setTimeout, clearTimeout,
    requestAnimationFrame: () => 0
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP_JS, 'utf8') + '\n;globalThis.__d = dashboard;', sandbox);
  const comp = sandbox.__d();
  // 容器尺寸由用例给定：宽高为 0 的隐藏容器必须在 getChart 里被挡掉
  comp.__el = (id, w, h) => { elements[id] = { __id: id, clientWidth: w, clientHeight: h }; };
  return { comp, captured, calls, settings };
}

/* --- 样例数据（结构与后端真实响应一致，时间均为 UTC `...Z`）-------------------- */

const iso = minutesAgo => new Date(Date.now() - minutesAgo * 60000).toISOString().replace(/\.\d{3}Z$/, 'Z');

function trendPoints({ count, stepMinutes, bucket }) {
  const points = [];
  for (let i = count - 1; i >= 0; i--) {
    const linux = Math.round(40 + 25 * Math.sin(i / 3));
    const windows = Math.round(180 + 90 * Math.cos(i / 4));
    points.push({ bucket: iso(i * stepMinutes).slice(0, 19) + 'Z', linux, windows, total: linux + windows });
  }
  return { bucket, range: bucket === 'day' ? '30d' : '24h', generatedAt: iso(0), cached: true, ageMs: 4200, points };
}

const SUMMARY = {
  total: 425812, by_platform: { Linux: 160552, Windows: 265260 },
  categories: [
    { platform: 'Linux', label: '认证会话', count: 160526 },
    { platform: 'Linux', label: '账号安全变更', count: 0 },
    { platform: 'Windows', label: 'Security', count: 265260 }
  ]
};

const CLIENTS = [
  { clientId: 'sim-linux-001', heartbeatDelaySec: 4, heartbeatStatus: 'ONLINE' },
  { clientId: 'sim-linux-002', heartbeatDelaySec: 12, heartbeatStatus: 'DELAYED' },
  { clientId: 'sim-win-001', heartbeatDelaySec: 40, heartbeatStatus: 'OFFLINE' }
];

const HISTORY = {
  intervalSec: 5, windowMinutes: 15, samples: 3,
  points: [
    { t: iso(2), total: 3, online: 1, delayed: 1, offline: 1, avgDelay: 18.7 },
    { t: iso(1), total: 3, online: 2, delayed: 1, offline: 0, avgDelay: 9.3 },
    { t: iso(0), total: 3, online: 2, delayed: 1, offline: 0, avgDelay: 6.1 }
  ],
  clients: [
    { clientId: 'sim-linux-001', status: 'ONLINE', points: [[iso(2), 4], [iso(1), 3], [iso(0), 3]] },
    { clientId: 'sim-linux-002', status: 'DELAYED', points: [[iso(2), 12], [iso(1), 9], [iso(0), 6]] },
    { clientId: 'sim-win-001', status: 'ONLINE', points: [[iso(2), 40], [iso(1), 16], [iso(0), 9]] }
  ]
};

// 一个已掉线的客户端延迟 45 秒：它不得出现在曲线里，也不得参与平均
const OFFLINE_HISTORY = {
  intervalSec: 5, windowMinutes: 15, samples: 2,
  points: [
    { t: iso(1), total: 2, online: 1, delayed: 0, offline: 1, avgDelay: 24.5 },
    { t: iso(0), total: 2, online: 1, delayed: 0, offline: 1, avgDelay: 24.5 }
  ],
  clients: [
    { clientId: 'sim-linux-001', status: 'ONLINE', points: [[iso(1), 4], [iso(0), 6]] },
    { clientId: 'sim-win-999', status: 'OFFLINE', points: [[iso(1), 45], [iso(0), 45]] }
  ]
};

const STATS = {
  range: '24h', generatedAt: iso(0), cached: false, ageMs: 0,
  topCategories: [
    { key: 'security', label: 'Security', platform: 'Windows', count: 82031 },
    { key: 'authentication_session', label: '认证会话', platform: 'Linux', count: 41200 },
    { key: 'system', label: 'System', platform: 'Windows', count: 12040 }
  ],
  topHosts: [
    { host: 'server-a', count: 15022 },
    { host: 'win-corp-07', count: 9831 },
    { host: 'server-b', count: 4210 }
  ]
};

const SECURITY_ROWS = [
  { event: 'SQL 注入', ruleid: 0, threat_type: 'Web 攻击', count: 100 },
  { event: 'SQL 注入', ruleid: 1001, threat_type: 'Web 攻击', count: 1 },
  { event: '异常访问', ruleid: 1001, threat_type: 'Web 攻击', count: 5 },
  { event: null, ruleid: null, threat_type: null, count: 0 }
];

/* --- 用例 -------------------------------------------------------------------- */

const CASES = [
  ...['security-ips', 'security-blacklist', 'security-web'].flatMap(page => [
    {
      name: `${page} · 事件信息统计`, id: 'security-event-chart', page, size: [620, 300],
      setup: c => { c.securityRows = SECURITY_ROWS; }, draw: c => c.drawSecurityEvents(),
      expectText: ['本页日志', 'SQL 注入', '异常访问', '未提供']
    },
    {
      name: `${page} · 规则 / 威胁类型统计`, id: 'security-rule-chart', page, size: [620, 300],
      setup: c => { c.securityRows = SECURITY_ROWS; }, draw: c => c.drawSecurityRules(),
      expectText: ['0 / Web 攻击', '1001 / Web 攻击', '未提供 / 未提供']
    }
  ]),
  ...['event', 'rule'].flatMap(kind => [
    {
      name: `日志审计 · ${kind} 分布（空态）`, id: `security-${kind}-chart`, size: [320, 300], empty: true,
      setup: c => { c.securityRows = []; }, draw: c => kind === 'event' ? c.drawSecurityEvents() : c.drawSecurityRules()
    },
    ...[320, 620].map(width => ({
      name: `日志审计 · ${kind} 分布（${width}px 长名称与长尾）`, id: `security-${kind}-chart`, size: [width, 300],
      setup: c => { c.securityRows = Array.from({ length: 30 }, (_, i) => ({ event: '很长的安全日志事件名称-' + i, ruleid: i, threat_type: '很长的威胁类型描述-' + i })); },
      draw: c => kind === 'event' ? c.drawSecurityEvents() : c.drawSecurityRules(),
      expectText: [kind === 'event' ? '本页日志' : '其他规则']
    }))
  ]),
  {
    name: '概览 · 实时接入趋势', id: 'trend-chart', size: [820, 300],
    setup: c => { c.trend = trendPoints({ count: 60, stepMinutes: 1, bucket: 'minute' }); },
    draw: c => c.drawOverviewTrend()
  },
  {
    name: '概览 · 实时接入趋势（空态）', id: 'trend-chart', size: [820, 300], empty: true,
    setup: c => { c.trend = { bucket: 'minute', points: [] }; },
    draw: c => c.drawOverviewTrend()
  },
  {
    name: '概览 · 平台占比半环仪表', id: 'platform-gauge', size: [380, 300],
    setup: c => { c.summary = SUMMARY; },
    draw: c => c.drawPlatformGauge(),
    // 半环中心的总量由哑系列 label 提供，必须真的画出来
    expectText: ['425,812', '日志总量']
  },
  {
    name: '概览 · 平台占比半环仪表（空态）', id: 'platform-gauge', size: [380, 300], empty: true,
    setup: c => { c.summary = { total: 0, by_platform: { Linux: 0, Windows: 0 }, categories: [] }; },
    draw: c => c.drawPlatformGauge()
  },
  {
    name: '心跳 · 延迟趋势', id: 'delay-chart', size: [820, 300],
    setup: c => { c.history = HISTORY; },
    draw: c => c.drawDelayChart()
  },
  {
    name: '心跳 · 延迟趋势（进程刚重启，无采样）', id: 'delay-chart', size: [820, 300], empty: true,
    setup: c => { c.history = { intervalSec: 5, windowMinutes: 15, samples: 0, points: [], clients: [] }; },
    draw: c => c.drawDelayChart()
  },
  {
    name: '心跳 · 延迟趋势（离线客户端不进入曲线）', id: 'delay-chart', size: [820, 300], offline: true,
    setup: c => { c.history = OFFLINE_HISTORY; },
    draw: c => c.drawDelayChart()
  },
  {
    // 延迟逼近 10 秒阈值：纵轴抬到 10 秒档并画出参考线，此时参考线标签在最靠上的位置，
    // 是最容易与图例/画布边缘打架的布局，所以单独立一个用例跑完整的渲染与越界检查
    name: '心跳 · 延迟趋势（延迟逼近阈值，出现 10 秒参考线）', id: 'delay-chart', size: [820, 300],
    setup: c => {
      const near = [];
      for (let i = 0; i < 60; i++) near.push([iso(60 - i), 6.5 + (i % 4) * 0.3]);
      c.history = {
        intervalSec: 5, windowMinutes: 15, samples: 60,
        points: near.map(([t]) => ({ t, total: 2, online: 2, delayed: 0, offline: 0, avgDelay: 7 })),
        clients: [{ clientId: 'sim-linux-001', status: 'ONLINE', points: near }]
      };
    },
    draw: c => c.drawDelayChart(),
    expectText: ['延迟阈值 10s']
  },
  {
    name: '心跳 · 状态分布甜甜圈', id: 'status-donut', size: [380, 300],
    setup: c => { c.hbCounts = { total: 3, online: 1, delayed: 1, offline: 1 }; },
    draw: c => c.drawStatusDonut(),
    expectText: ['客户端']
  },
  {
    name: '心跳 · 状态分布甜甜圈（空态）', id: 'status-donut', size: [380, 300], empty: true,
    setup: c => { c.hbCounts = { total: 0, online: 0, delayed: 0, offline: 0 }; },
    draw: c => c.drawStatusDonut()
  },
  {
    name: '统计 · 接入趋势（双平台）', id: 'stats-trend-chart', size: [820, 300],
    setup: c => { c.statsPlatform = ''; c.statsRange = '7d'; c.statsTrend = trendPoints({ count: 168, stepMinutes: 60, bucket: 'hour' }); },
    draw: c => c.drawStatsTrend()
  },
  {
    name: '统计 · 接入趋势（已筛选平台，单序列）', id: 'stats-trend-chart', size: [820, 300],
    setup: c => { c.statsPlatform = 'Windows'; c.statsRange = '30d'; c.statsTrend = trendPoints({ count: 30, stepMinutes: 1440, bucket: 'day' }); },
    draw: c => c.drawStatsTrend()
  },
  {
    name: '统计 · 接入趋势（空态）', id: 'stats-trend-chart', size: [820, 300], empty: true,
    setup: c => { c.statsTrend = { bucket: 'hour', points: [] }; },
    draw: c => c.drawStatsTrend()
  },
  {
    name: '统计 · 平台占比甜甜圈（双平台）', id: 'stats-platform-chart', size: [380, 300],
    setup: c => { c.statsPlatform = ''; c.statsTrend = trendPoints({ count: 24, stepMinutes: 60, bucket: 'hour' }); },
    draw: c => c.drawStatsPlatform(),
    expectText: ['窗口内合计']
  },
  {
    name: '统计 · 平台占比甜甜圈（单平台筛选）', id: 'stats-platform-chart', size: [380, 300],
    setup: c => { c.statsPlatform = 'Linux'; c.statsTrend = trendPoints({ count: 24, stepMinutes: 60, bucket: 'hour' }); },
    draw: c => c.drawStatsPlatform()
  },
  {
    name: '统计 · 平台占比甜甜圈（空态）', id: 'stats-platform-chart', size: [380, 300], empty: true,
    setup: c => { c.statsPlatform = ''; c.statsTrend = { bucket: 'hour', points: [] }; },
    draw: c => c.drawStatsPlatform()
  },
  {
    name: '统计 · 7×24 热力图', id: 'stats-heatmap', size: [700, 300], heatmap: true,
    setup: c => { c.statsHeat = trendPoints({ count: 168, stepMinutes: 60, bucket: 'hour' }); },
    draw: c => c.drawStatsHeatmap()
  },
  {
    name: '统计 · 7×24 热力图（空态）', id: 'stats-heatmap', size: [700, 300], empty: true,
    setup: c => { c.statsHeat = { bucket: 'hour', points: [] }; },
    draw: c => c.drawStatsHeatmap()
  },
  {
    name: '统计 · TOP 主机排行', id: 'stats-hosts-chart', size: [500, 300],
    setup: c => { c.statsRank = STATS; },
    draw: c => c.drawStatsHosts()
  },
  {
    name: '统计 · TOP 主机排行（空态）', id: 'stats-hosts-chart', size: [500, 300], empty: true,
    setup: c => { c.statsRank = { topCategories: [], topHosts: [] }; },
    draw: c => c.drawStatsHosts()
  },
  {
    name: '统计 · TOP 类别排行', id: 'stats-categories-chart', size: [1200, 240],
    setup: c => { c.statsRank = STATS; },
    draw: c => c.drawStatsCategories()
  },
  {
    name: '统计 · TOP 类别排行（空态）', id: 'stats-categories-chart', size: [1200, 240], empty: true,
    setup: c => { c.statsRank = { topCategories: [], topHosts: [] }; },
    draw: c => c.drawStatsCategories()
  }
];

/* --- 执行 -------------------------------------------------------------------- */

let failures = 0;
function check(ok, label, detail) {
  if (!ok) failures++;
  console.log(`    ${ok ? '[OK]  ' : '[FAIL]'} ${label}${detail ? ' — ' + detail : ''}`);
}

function svgTexts(svg) {
  const out = [];
  const re = /<text[^>]*>([^<]*)<\/text>/g;
  let m;
  while ((m = re.exec(svg))) out.push(m[1]);
  return out;
}

// 文字宽度按字符估算（与 tools/chart-layout-check.cjs 同一套口径，ASCII 系数按 ECharts
// 实际字体度量校准到 0.5，否则右对齐的轴标签会被算出十几像素的假越界）
function textWidth(text, fontSize) {
  let w = 0;
  for (const ch of text) {
    const c = ch.codePointAt(0);
    const wide = (c >= 0x2e80 && c <= 0x9fff) || (c >= 0x3000 && c <= 0x303f) || (c >= 0xff00 && c <= 0xffef);
    w += wide ? fontSize : fontSize * 0.5;
  }
  return w;
}

/** SVG 里所有 <text> 的包围盒：位置可能来自 transform="translate(x y)"，也可能来自局部 x/y。 */
function textBoxes(svg) {
  const out = [];
  const re = /<text([^>]*)>([^<]*)<\/text>/g;
  let m;
  while ((m = re.exec(svg))) {
    const attrs = m[1], content = m[2];
    if (!content.trim()) continue;
    const tr = /transform="translate\(([-\d.]+)[ ,]+([-\d.]+)\)"/.exec(attrs);
    const lx = /(?:^|\s)x="([-\d.]+)"/.exec(attrs);
    const ly = /(?:^|\s)y="([-\d.]+)"/.exec(attrs);
    const fs = /font-size:\s*([\d.]+)px/.exec(attrs) || /font-size="([\d.]+)"/.exec(attrs);
    const fontSize = fs ? parseFloat(fs[1]) : 12;
    const anchor = (/text-anchor="(\w+)"/.exec(attrs) || [, 'start'])[1];
    const x = (tr ? parseFloat(tr[1]) : 0) + (lx ? parseFloat(lx[1]) : 0);
    const y = (tr ? parseFloat(tr[2]) : 0) + (ly ? parseFloat(ly[1]) : 0);
    const width = textWidth(content, fontSize);
    const left = anchor === 'end' ? x - width : anchor === 'middle' ? x - width / 2 : x;
    out.push({ content, left, right: left + width, top: y - fontSize / 2, bottom: y + fontSize / 2 });
  }
  return out;
}

console.log(`新图表渲染自检：${CASES.length} 个用例（真实 app.js + 真实 ECharts 服务端渲染）`);

function runCase(item) {
  console.log(`\n=== ${item.name} ===`);
  const { comp, captured, calls, settings } = makeHarness();
  const [w, h] = item.size;
  comp.__el(item.id, w, h);
  comp.summary = SUMMARY;
  comp.page = item.page || 'overview';
  item.setup(comp);
  let thrown = null;
  try {
    item.draw(comp);
  } catch (err) {
    thrown = err;
  }
  check(!thrown, '绘制函数未抛异常', thrown ? String(thrown && thrown.message || thrown) : '');
  const option = captured[item.id];
  check(!!option, '产出了 option');
  if (!option) return;
  check(calls[item.id] === 1, '一次绘制只 setOption 一次（统一走 paint 出口）', `实际 ${calls[item.id]} 次`);
  check(!!settings[item.id] && Array.isArray(settings[item.id].replaceMerge),
    '首次绘制用 replaceMerge 建立 series 结构');

  let svg = '';
  try {
    const chart = echartsLib.init(null, null, { renderer: 'svg', ssr: true, width: w, height: h });
    chart.setOption(option);
    svg = chart.renderToSVGString();
    chart.dispose();
  } catch (err) {
    thrown = err;
  }
  check(!thrown, 'ECharts 能渲染该 option', thrown ? String(thrown && thrown.message || thrown) : `${svg.length} 字节 SVG`);
  if (thrown) return;

  const texts = svgTexts(svg);
  const marks = (svg.match(/<path|<rect|<polyline|<circle/g) || []).length;
  if (item.empty) {
    // 空态必须画字：一张完全空白的画布与「加载失败」在肉眼上无法区分
    check(texts.some(t => t.includes('暂无')), '空数据画出了「暂无」空态文字', texts.join('|') || '（无文字）');
  } else {
    check(marks > 0, '画布上有实际图形', `${marks} 个图元`);
  }
  (item.expectText || []).forEach(expected => {
    check(texts.some(t => t.includes(expected)), `画出了「${expected}」`, texts.join('|') || '（无文字）');
  });
  // 轴标签/图例被顶出画布是最常见的「图表看着缺一块」，这里统一兜住（6px 容差给文字度量误差）
  const boxes = textBoxes(svg);
  const outside = boxes.filter(b => b.left < -6 || b.right > w + 6 || b.top < -6 || b.bottom > h + 6);
  check(outside.length === 0, '文字内容都在画布内（未被裁掉）',
    outside.length ? outside.slice(0, 3).map(b => `「${b.content}」x[${Math.round(b.left)},${Math.round(b.right)}] y[${Math.round(b.top)},${Math.round(b.bottom)}]`).join('；') : `${boxes.length} 段文字`);
  if (item.offline) {
    // 掉线的机器不参与延迟展示：曲线里没有它，平均线也不能把它算进去
    const names = (option.series || []).map(s => s.name);
    check(!names.some(n => /999/.test(String(n))), '离线客户端没有进入图表', names.join('|'));
    const average = (option.series || []).find(s => s.name === '平均延迟');
    if (average) {
      check(average.data.every(v => v === null || Number(v) < 30),
        '平均线只由未掉线客户端计算（离线的高延迟没有被算进去）', JSON.stringify(average.data));
    }
  }
  if (item.heatmap) {
    const data = (option.series || []).find(s => s.type === 'heatmap');
    const n = data ? data.data.length : 0;
    check(n === 168, '热力图矩阵是 7×24 = 168 格', `${n} 格`);
    const values = data ? data.data.map(d => d[2]) : [];
    check(values.some(v => v > 0), '热力图按本地时区聚合出了非 0 值');
    check(!!option.visualMap && option.visualMap.max > 0, 'visualMap 上限取自真实峰值', option.visualMap ? `max=${option.visualMap.max}` : '');
  }
}

/* --- --live：用运行中服务的真实响应再跑一遍（形状不符会在这里暴露）------------- */

async function liveCases(base) {
  const get = async (p) => {
    const r = await fetch(base + p, { signal: AbortSignal.timeout(8000) });
    if (!r.ok) throw Error(`HTTP ${r.status} ${p}`);
    return (await r.json()).data;
  };
  const kinds = ['ipslog', 'ipblacklistlog', 'ips_web_secure_event_log'];
  const [trend, heat, stats, history, ...securityLists] = await Promise.all([
    get('/api/v1/logs/trend?range=1h&bucket=minute'),
    get('/api/v1/logs/trend?range=7d&bucket=hour'),
    get('/api/v1/logs/stats?range=7d&limit=10'),
    get('/api/v1/monitor/history?minutes=15'),
    ...kinds.map(kind => get('/api/v1/security/logs/' + kind + '?limit=30'))
  ]);
  if (securityLists.some(list => !Array.isArray(list.items))) throw Error('安全日志列表缺少 items 数组');
  const live = (name, id, size, setup, draw, extra) =>
    Object.assign({ name: `[真实数据] ${name}`, id, size, setup, draw }, extra || {});
  return [
    ...kinds.flatMap((kind, i) => [
      live(`${kind} · 事件信息统计`, 'security-event-chart', [620, 300], c => { c.securityRows = securityLists[i].items; }, c => c.drawSecurityEvents()),
      live(`${kind} · 规则 / 威胁类型统计`, 'security-rule-chart', [620, 300], c => { c.securityRows = securityLists[i].items; }, c => c.drawSecurityRules())
    ]),
    live('概览 · 实时接入趋势', 'trend-chart', [820, 300], c => { c.trend = trend; }, c => c.drawOverviewTrend()),
    live('统计 · 接入趋势', 'stats-trend-chart', [820, 300], c => { c.statsPlatform = ''; c.statsTrend = heat; }, c => c.drawStatsTrend()),
    live('统计 · 平台占比甜甜圈', 'stats-platform-chart', [380, 300], c => { c.statsPlatform = ''; c.statsTrend = heat; }, c => c.drawStatsPlatform()),
    live('统计 · 7×24 热力图', 'stats-heatmap', [700, 300], c => { c.statsHeat = heat; }, c => c.drawStatsHeatmap(), { heatmap: true }),
    live('统计 · TOP 主机排行', 'stats-hosts-chart', [500, 300], c => { c.statsRank = stats; }, c => c.drawStatsHosts()),
    live('统计 · TOP 类别排行', 'stats-categories-chart', [1200, 240], c => { c.statsRank = stats; }, c => c.drawStatsCategories()),
    live('心跳 · 延迟趋势', 'delay-chart', [820, 300], c => { c.history = history; c.page = 'heartbeat'; }, c => c.drawDelayChart()),
    live('心跳 · 状态分布', 'status-donut', [380, 300], c => {
      c.page = 'heartbeat';
      const last = history.points.at(-1);
      c.hbCounts = last
        ? { total: last.total, online: last.online, delayed: last.delayed, offline: last.offline }
        : { total: 0, online: 0, delayed: 0, offline: 0 };
    }, c => c.drawStatusDonut())
  ];
}

(async () => {
for (const item of CASES) runCase(item);

if (liveBase) {
  console.log(`\n########## 真实接口数据（${liveBase}）##########`);
  try {
    for (const item of await liveCases(liveBase)) runCase(item);
  } catch (err) {
    failures++;
    console.log(`    [FAIL] 真实接口取数失败 — ${err && err.message || err}`);
  }
}

console.log(`\n=== 重复推送（WebSocket 每 5 秒一轮快照）不得重播整图 ===`);
{
  const { comp, calls, settings } = makeHarness();
  comp.__el('status-donut', 380, 300);
  comp.__el('delay-chart', 820, 300);
  comp.hbCounts = { total: 3, online: 1, delayed: 1, offline: 1 };
  comp.history = JSON.parse(JSON.stringify(HISTORY));
  comp.drawHeartbeatCharts();
  comp.drawDelayChart();
  const first = Object.assign({}, calls);

  // ① 快照完全没变（watchdog 每轮都推，多数轮次数据相同）：一根线都不该重画
  comp.drawHeartbeatCharts();
  comp.drawDelayChart();
  check(calls['status-donut'] === first['status-donut'] && calls['delay-chart'] === first['delay-chart'],
    '数据没变时不再 setOption（这是「整页一直在闪」的根因）',
    `甜甜圈 ${first['status-donut']}→${calls['status-donut']}；延迟 ${first['delay-chart']}→${calls['delay-chart']}`);

  // ② 数值变了：只做合并更新（第二个参数为空 → ECharts 走 animationDurationUpdate，不重播入场动画）
  comp.hbCounts = { total: 3, online: 2, delayed: 1, offline: 0 };
  comp.drawHeartbeatCharts();
  check(calls['status-donut'] === first['status-donut'] + 1, '数值变化时会重画一次');
  check(!settings['status-donut'], '数值变化走合并更新（不传 replaceMerge，不重播入场动画）',
    JSON.stringify(settings['status-donut'] || null));

  // ③ 结构变了（未掉线客户端增减）：必须 replaceMerge，否则旧曲线会留在图上
  comp.history.clients.push({ clientId: 'sim-win-002', status: 'ONLINE', points: [[iso(1), 7], [iso(0), 5]] });
  comp.history.samples = 4;
  const beforeStructure = calls['delay-chart'];
  comp.drawDelayChart();
  check(calls['delay-chart'] === beforeStructure + 1
    && !!settings['delay-chart'] && Array.isArray(settings['delay-chart'].replaceMerge),
    '客户端增减时用 replaceMerge 重建序列（否则旧曲线残留）',
    JSON.stringify(settings['delay-chart'] || null));

  // ④ 生产环境最常见的「每 5 秒多一个采样点」：结构没变，必须合并更新（曲线平滑左移），不能重播动画
  const twoPoints = {
    intervalSec: 5, windowMinutes: 15, samples: 2,
    points: [
      { t: iso(1), total: 1, online: 1, delayed: 0, offline: 0, avgDelay: 4 },
      { t: iso(0), total: 1, online: 1, delayed: 0, offline: 0, avgDelay: 6 }
    ],
    clients: [{ clientId: 'sim-linux-001', status: 'ONLINE', points: [[iso(1), 4], [iso(0), 6]] }]
  };
  const threePoints = JSON.parse(JSON.stringify(twoPoints));
  threePoints.samples = 3;
  threePoints.points.unshift({ t: iso(2), total: 1, online: 1, delayed: 0, offline: 0, avgDelay: 5 });
  threePoints.clients[0].points.unshift([iso(2), 5]);
  comp.history = twoPoints;
  comp.drawDelayChart();
  const beforeGrow = calls['delay-chart'];
  comp.history = threePoints;
  comp.drawDelayChart();
  check(calls['delay-chart'] === beforeGrow + 1, '新增采样点会重画一次');
  check(!settings['delay-chart'], '新增采样点走合并更新（曲线平滑左移，不重播入场动画）',
    JSON.stringify(settings['delay-chart'] || null));
}

console.log(`\n=== 心跳延迟曲线按原始采样出折线（不做平滑）===`);
{
  const { comp, captured } = makeHarness();
  comp.__el('delay-chart', 820, 300);
  // 4 秒 ↔ 20 秒交替：做过平滑的版本相邻点最多只跳 1 秒，原始折线必须如实跳出 16 秒，
  // 这条断言就是「平滑有没有被偷偷加回来」的探针。
  const raw = [];
  for (let i = 0; i < 40; i++) raw.push([iso(40 - i), i % 2 ? 4 : 20]);
  comp.history = {
    intervalSec: 5, windowMinutes: 15, samples: 40,
    points: raw.map(([t]) => ({ t, total: 1, online: 1, delayed: 0, offline: 0, avgDelay: 12 })),
    clients: [{ clientId: 'sim-linux-001', status: 'ONLINE', points: raw }]
  };
  comp.drawDelayChart();
  const option = captured['delay-chart'] || {};
  const series = (option.series || []).find(s => s.name === 'sim-linux-001');
  const data = series ? series.data : [];
  const expected = raw.map(([, v]) => v);
  check(data.length === 40 && data.every((v, i) => v === expected[i]),
    '客户端曲线与原始采样逐点一致（没有被平滑改写）',
    `前 6 个点：${data.slice(0, 6).join(',')}`);
  let maxJump = 0;
  for (let i = 1; i < data.length; i++) {
    if (data[i] == null || data[i - 1] == null) continue;
    maxJump = Math.max(maxJump, Math.abs(data[i] - data[i - 1]));
  }
  check(Math.abs(maxJump - 16) < 0.01, '4↔20 秒的抖动如实呈现（相邻跳变 16 秒，未被压平）',
    `最大相邻跳变 ${maxJump.toFixed(1)} 秒`);
  check(series && series.smooth === false, '折线用直线段连接（smooth:false），不是样条');
  const avg = (option.series || []).find(s => s.name === '平均延迟');
  check(!!avg && avg.smooth === false, '平均线同样是折线');
  check(!!avg && typeof avg.areaStyle === 'object', '仍是渐变面积 + 折线的观感（视觉不变，只去掉平滑）');
}

console.log(`\n=== 心跳延迟趋势的视觉与纵轴锚定 ===`);
{
  const { comp, captured } = makeHarness();
  comp.__el('delay-chart', 820, 300);
  // 场景一：延迟只在 3.0~3.8 秒之间波动。自动缩放的纵轴会把这点波动撑满整个高度，
  // 看起来像剧烈震荡——这正是「抖动很大」的真正来源，所以纵轴必须锚定。
  const calm = [];
  for (let i = 0; i < 60; i++) calm.push([iso(60 - i), 3 + (i % 5) * 0.2]);
  comp.history = {
    intervalSec: 5, windowMinutes: 15, samples: 60,
    points: calm.map(([t]) => ({ t, total: 2, online: 2, delayed: 0, offline: 0, avgDelay: 3.4 })),
    clients: [{ clientId: 'sim-linux-001', status: 'ONLINE', points: calm }]
  };
  comp.drawDelayChart();
  const option = captured['delay-chart'] || {};
  const average = (option.series || []).find(s => s.name === '平均延迟') || {};
  const thinLine = (option.series || []).find(s => s.name === 'sim-linux-001') || {};
  check(!!average.areaStyle && typeof average.areaStyle.color === 'object',
    '平均延迟是渐变面积曲线（与概览「接入趋势」同一套视觉，不是平涂色块）',
    JSON.stringify(average.areaStyle && typeof average.areaStyle.color));
  check(option.yAxis && option.yAxis.min === 0 && Number.isFinite(option.yAxis.max),
    '纵轴锚定 0 与「凑整上限」，不自动缩放（自动缩放是小波动被放大成剧烈震荡的元凶）',
    `min=${option.yAxis && option.yAxis.min} max=${option.yAxis && option.yAxis.max}`);
  check(option.yAxis.max <= 10, '3~4 秒的波动不会把纵轴撑到 10 秒以上', `max=${option.yAxis.max}`);
  check(!!thinLine.lineStyle && thinLine.lineStyle.width <= 1 && thinLine.lineStyle.opacity <= 0.6,
    '客户端细线退到背景（细 + 半透明，hover 才加粗）', JSON.stringify(thinLine.lineStyle));
  // 主视线只比背景细线粗一档（2px vs 1px）：2026-10-13 按需求从 3px 收细
  check(!!average.lineStyle && average.lineStyle.width > thinLine.lineStyle.width && average.lineStyle.width <= 2,
    '平均延迟线只比客户端细线粗一档（≤ 2px）', JSON.stringify(average.lineStyle));
  check(!(((average.markLine || {}).data) || []).length, '小延迟场景不画 10 秒参考线（否则把曲线压平）');

  // 场景二：延迟升到 20 秒上下 → 纵轴按整数档抬高，并画出 10 秒阈值参考线
  const high = calm.map(([t], i) => [t, 20 + (i % 4)]);
  comp.history = Object.assign({}, comp.history, {
    clients: [{ clientId: 'sim-linux-001', status: 'ONLINE', points: high }]
  });
  comp.drawDelayChart();
  const raised = captured['delay-chart'] || {};
  const raisedAvg = (raised.series || []).find(s => s.name === '平均延迟') || {};
  check(raised.yAxis.max >= 20 && raised.yAxis.max <= 60, '延迟升高后纵轴按整数档抬高', `max=${raised.yAxis.max}`);
  check((((raisedAvg.markLine || {}).data) || []).length === 1, '延迟超过阈值时画出 10 秒参考线（对应心跳状态阈值）');
}

console.log(`\n=== 日志审计分类图：真实记录口径、原值与重复刷新 ===`);
{
  const { comp, calls, captured } = makeHarness();
  comp.page = 'security-ips';
  comp.__el('security-event-chart', 620, 300);
  comp.__el('security-rule-chart', 620, 300);
  comp.securityRows = SECURITY_ROWS;
  comp.drawSecurityCharts();
  const events = captured['security-event-chart'].series[0].data;
  check(events.reduce((sum, row) => sum + row.value, 0) === 4 && events.find(r => r.name === 'SQL 注入').value === 2, '按事件名称归组，每条记录计 1 次，不累加 count');
  const rules = captured['security-rule-chart'];
  check(rules.yAxis.data.includes('0 / Web 攻击'), '规则 0 保留为有效值');
  check(rules.yAxis.data.includes('未提供 / 未提供'), '缺失规则和威胁类型仍计入统计');
  check(rules.series[0].data[rules.yAxis.data.indexOf('1001 / Web 攻击')] === 2, '相同规则与威胁类型组合合并计数');
  const originalRows = comp.securityRows;
  comp.securityRows = [{ ruleid: 1001, threat_type: 'A' }, { ruleid: 1001, threat_type: 'B' }, { ruleid: 1002, threat_type: 'A' }];
  check(comp.securityDistribution(['ruleid', 'threat_type']).length === 3, '规则或威胁类型不同的记录分别统计');
  comp.securityRows = originalRows;
  check(comp.securityChartNote().includes('当前页 4 条') && comp.securityChartNote().includes('不代表分类全量'), '图旁说明当前页统计范围');
  comp.drawSecurityCharts();
  check(calls['security-event-chart'] === 1 && calls['security-rule-chart'] === 1, '相同数据刷新不会重画两张分类图');
  comp.securityRows = [{ event: '恶意域名访问', ruleid: 5001, threat_type: 'DNS 黑名单' }];
  comp.page = 'security-blacklist';
  comp.drawSecurityCharts();
  check(captured['security-event-chart'].series[0].data[0].name === '恶意域名访问', '切换分类后只展示新分类数据');
  comp.securityRows = [];
  comp.securityError = 'HTTP 503';
  comp.drawSecurityCharts();
  check(captured['security-event-chart'].title.text === '日志读取失败' && captured['security-rule-chart'].title.text === '日志读取失败', '读取失败展示故障态，不伪装成零日志');
}

console.log(`\n=== 实例被重建后必须重新绘制 ===`);
{
  // 回归：getChart 重建实例时要一起清掉绘制指纹，否则新实例会被「数据没变」判为无需绘制 → 空白图
  const { comp, calls } = makeHarness();
  comp.__el('status-donut', 380, 300);
  comp.hbCounts = { total: 3, online: 1, delayed: 1, offline: 1 };
  comp.drawStatusDonut();
  const before = calls['status-donut'];
  const original = comp.charts['status-donut'];
  // 模拟被外部销毁（例如手动 dispose 后又回到该页）
  comp.charts['status-donut'] = {
    isDisposed: () => true, getDom: () => original.getDom(),
    resize() {}, setOption(option) { calls['status-donut'] = (calls['status-donut'] || 0) + 1; }
  };
  comp.drawStatusDonut();
  check(calls['status-donut'] === before + 1, '实例被销毁重建后会重新绘制（不会留下空白图）',
    `${before} → ${calls['status-donut']}`);
}

console.log(`\n=== 概览「哈希存证」面板与侧栏连接卡的状态文案 ===`);
{
  const { comp } = makeHarness();
  // 侧栏「实时通道」行：三种连接状态都必须有对应的颜色与文案
  comp.wsStatus = 'connected';
  check(comp.wsTone() === 'ok' && comp.wsLabel() === '已连接' && comp.wsHint() === '5 秒推送', '实时通道：已连接');
  comp.wsStatus = 'connecting';
  check(comp.wsTone() === 'warn' && comp.wsLabel() === '连接中…', '实时通道：连接中');
  comp.wsStatus = 'disconnected';
  check(comp.wsTone() === 'bad' && comp.wsLabel() === '已断开' && comp.wsHint() === '5 秒轮询', '实时通道：已断开（降级轮询）');

  // 哈希存证面板：正常 / 原始缺失 / 哈希异常 / 查询失败（接口 503）
  comp.hashTotal = 34099;
  comp.hashStatuses = { VALID: 34099, HASH_MISMATCH: 0, RAW_MISSING: 0, PENDING: 0 };
  check(comp.hashTone() === 'ok' && comp.hashPillText() === '完整率 100%', '存证正常：完整率 100%',
    `${comp.hashTone()} / ${comp.hashPillText()}`);
  check(comp.hashBar().ok === 100, '进度条按占比切分', JSON.stringify(comp.hashBar()));
  comp.hashStatuses = { VALID: 34097, HASH_MISMATCH: 0, RAW_MISSING: 2, PENDING: 0 };
  check(comp.hashTone() === 'warn' && comp.hashPillText().includes('原始缺失'), '原始缺失报橙');
  comp.hashStatuses = { VALID: 34097, HASH_MISMATCH: 2, RAW_MISSING: 0, PENDING: 0 };
  check(comp.hashTone() === 'bad' && comp.hashPillText().includes('哈希异常'), '哈希异常优先报红');
  // 失败态：必须显示「不可用」，绝不能显示成 0 条
  comp.hashError = '存证库查询失败（HTTP 503）：这不代表没有存证';
  comp.hashTotal = 0;
  comp.hashStatuses = null;
  comp.hashMetrics = [];
  check(comp.hashTone() === 'bad' && comp.hashPillText() === '存证库不可用', '查询失败显示「存证库不可用」');
  check(comp.metricValue('hashMetrics', '存证总数') === '—', '查询失败时数字显示 — 而不是 0');
  check(comp.hashBar().ok === 0 && comp.hashBar().bad === 0, '查询失败时进度条不会被画成「全绿」');
}

console.log(`\n=== 容器被 x-show 隐藏（宽高为 0）===`);
{
  const { comp, captured } = makeHarness();
  comp.__el('trend-chart', 0, 300);
  comp.__el('platform-gauge', 0, 300);
  comp.__el('stats-heatmap', 0, 300);
  comp.__el('security-event-chart', 0, 300);
  comp.__el('security-rule-chart', 0, 300);
  comp.trend = trendPoints({ count: 5, stepMinutes: 1, bucket: 'minute' });
  comp.summary = SUMMARY;
  comp.statsHeat = trendPoints({ count: 5, stepMinutes: 60, bucket: 'hour' });
  comp.drawOverviewTrend(); comp.drawPlatformGauge(); comp.drawStatsHeatmap(); comp.drawSecurityEvents(); comp.drawSecurityRules();
  check(Object.keys(captured).length === 0, '隐藏容器跳过绘制（不画 0×0）', `已画：${Object.keys(captured).join(',') || '无'}`);
}

console.log(`\n${failures ? `失败 ${failures} 项` : '全部通过'}`);
process.exit(failures ? 1 : 0);
})();

