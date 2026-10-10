#!/usr/bin/env node
/* 概览环形图布局自检（不依赖浏览器）。
 *
 * 背景：概览页的「日志类别分布」是一张 ECharts 环形图，历史上出现过图例与图形、
 * 外侧标签互相压住的问题（图例 12 项全塞在底部，长类别名又与环形图重叠）。
 * 本机沙箱里无法运行无头浏览器，所以这里用「真实 app.js 的 drawChart + 真实
 * frontend/vendor 的 ECharts 服务端渲染（SVG）」把布局算出来，再断言：
 *   1. drawChart 在容器可见时确实产出了 option（容器宽/高为 0 时必须提前返回，不能画成 0×0）；
 *   2. ECharts 实际画出的弧线圆心/半径与 option 声明的 center/radius 一致（防止百分比语义被写错）；
 *   3. 图例文本包围盒与「弧线 + 引导线 + 百分比标签」包围盒不相交；
 *   4. 所有绘制内容都在画布内（不被裁掉）；
 *   5. 0 值类别已从环形图剔除，且图例条目数等于非 0 类别数。
 *
 * 用法（仓库根目录）：node tools/chart-layout-check.cjs [--widths=420,640,827,1200] [--summary=summary.json]
 * 退出码 0 = 全部通过，1 = 有断言失败。
 * 见 docs/ARCHITECTURE.md「前端」与 docs/CHANGELOG.md。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const ECHARTS = path.join(ROOT, 'frontend', 'vendor', 'echarts-5.5.1.min.js');
const APP_JS = path.join(ROOT, 'frontend', 'app.js');
const CHART_HEIGHT = 300; // 与 dashboard.html 的 .chart{height:300px} 保持一致

const argv = process.argv.slice(2);
const argOf = name => {
  const hit = argv.find(a => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
};
const widths = (argOf('widths') || '420,640,827,1200').split(',').map(Number).filter(n => Number.isFinite(n));

// 默认样例＝线上真实分布（12 个类别，其中 6 个为 0），可用 --summary 指向 /api/v1/logs/summary 的 JSON
const samplePath = argOf('summary');
const sample = samplePath
  ? JSON.parse(fs.readFileSync(samplePath, 'utf8'))
  : {
      total: 425812,
      by_platform: { Linux: 160552, Windows: 265260 },
      categories: [
        { platform: 'Linux', label: '认证会话', count: 160526 },
        { platform: 'Linux', label: '账号安全变更', count: 0 },
        { platform: 'Linux', label: '进程与命令执行', count: 0 },
        { platform: 'Linux', label: '文件对象访问', count: 0 },
        { platform: 'Linux', label: '网络/IPC 通信', count: 0 },
        { platform: 'Linux', label: '系统服务/审计生命周期', count: 26 },
        { platform: 'Linux', label: '安全策略/配置变更', count: 0 },
        { platform: 'Windows', label: 'Application', count: 54231 },
        { platform: 'Windows', label: 'Security', count: 100750 },
        { platform: 'Windows', label: 'Setup', count: 3105 },
        { platform: 'Windows', label: 'System', count: 107174 },
        { platform: 'Windows', label: 'ForwardedEvents', count: 0 }
      ]
    };

/** 在 vm 里跑真实 app.js 的 drawChart，用桩接住 setOption 拿到的 option。 */
function buildOption(width, height) {
  const captured = {};
  const el = { clientWidth: width, clientHeight: height };
  const fakeChart = {
    isDisposed: () => false,
    getDom: () => el,
    resize() { captured.resized = (captured.resized || 0) + 1; },
    setOption(option) { captured.option = option; }
  };
  const stub = { init: () => fakeChart, getInstanceByDom: () => null };
  const sandbox = {
    window: { addEventListener() {}, echarts: stub },
    document: { getElementById: id => (id === 'category-chart' ? el : null) },
    echarts: stub, console, setTimeout, clearTimeout
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APP_JS, 'utf8') + '\n;globalThis.__d = dashboard;', sandbox);
  const comp = sandbox.__d();
  comp.summary = sample;
  comp.drawChart.call(comp);
  return { option: captured.option, note: comp.chartNote };
}

/** SVG 里的 <text>：位置可能来自 transform="translate(x y)"，也可能来自局部 x/y 属性。 */
function parseTexts(svg) {
  const out = [];
  const re = /<text([^>]*)>([^<]*)<\/text>/g;
  let m;
  while ((m = re.exec(svg))) {
    const attrs = m[1];
    const content = m[2];
    const tr = /transform="translate\(([-\d.]+)[ ,]+([-\d.]+)\)"/.exec(attrs);
    const lx = /(?:^|\s)x="([-\d.]+)"/.exec(attrs);
    const ly = /(?:^|\s)y="([-\d.]+)"/.exec(attrs);
    const fsMatch = /font-size:\s*([\d.]+)px/.exec(attrs) || /font-size="([\d.]+)"/.exec(attrs);
    const fontSize = fsMatch ? parseFloat(fsMatch[1]) : 12;
    const anchor = (/text-anchor="(\w+)"/.exec(attrs) || [, 'start'])[1];
    const fill = (/fill="(#[0-9a-fA-F]{3,8})"/.exec(attrs) || [, null])[1];
    const x = (tr ? parseFloat(tr[1]) : 0) + (lx ? parseFloat(lx[1]) : 0);
    const y = (tr ? parseFloat(tr[2]) : 0) + (ly ? parseFloat(ly[1]) : 0);
    const w = textWidth(content, fontSize);
    const left = anchor === 'end' ? x - w : anchor === 'middle' ? x - w / 2 : x;
    out.push({ content, fill, left, right: left + w, top: y - fontSize / 2, bottom: y + fontSize / 2 });
  }
  return out;
}

// ECharts 在本机不一定装了 Microsoft YaHei，用它自己的文字度量不如按字符宽度估算稳；
// 判定重叠只需要量级正确（实际净距在百像素级）。
function textWidth(text, fontSize) {
  let w = 0;
  for (const ch of text) {
    const c = ch.codePointAt(0);
    const wide = (c >= 0x2e80 && c <= 0x9fff) || (c >= 0x3000 && c <= 0x303f) || (c >= 0xff00 && c <= 0xffef);
    w += wide ? fontSize : fontSize * 0.56;
  }
  return w;
}

/** 饼图 path 的起点是弧起点而不是圆心：起点在正上方时圆心 = (x, y + 外半径)。 */
function firstArc(svg) {
  const m = /<path d="M([-\d.]+) ([-\d.]+)A([\d.]+) [\d.]+ 0 [01] [01] [-\d.]+ [-\d.]+L/.exec(svg);
  if (!m) return null;
  const sx = parseFloat(m[1]), sy = parseFloat(m[2]), outer = parseFloat(m[3]);
  return { cx: sx, cy: sy + outer, outer };
}

function polylinePoints(svg) {
  const res = [];
  const re = /<polyline points="([^"]+)"/g;
  let m;
  while ((m = re.exec(svg))) {
    const nums = m[1].trim().split(/[\s,]+/).map(Number);
    for (let i = 0; i + 1 < nums.length; i += 2) res.push({ left: nums[i], right: nums[i], top: nums[i + 1], bottom: nums[i + 1] });
  }
  return res;
}

const bounds = items => items.reduce((a, b) => ({
  left: Math.min(a.left, b.left), right: Math.max(a.right, b.right),
  top: Math.min(a.top, b.top), bottom: Math.max(a.bottom, b.bottom)
}), { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity });
const intersects = (a, b) => !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
const r1 = v => Math.round(v * 10) / 10;

let failures = 0;
function assertOk(ok, label, detail) {
  if (!ok) failures++;
  console.log(`  ${ok ? '[OK]  ' : '[FAIL]'} ${label}${detail ? ' — ' + detail : ''}`);
}

function checkWidth(width) {
  console.log(`\n=== 容器 ${width}x${CHART_HEIGHT} ===`);
  const { option, note } = buildOption(width, CHART_HEIGHT);
  if (!option) {
    assertOk(false, 'drawChart 产出了 option');
    return;
  }
  const echarts = require(ECHARTS);
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width, height: CHART_HEIGHT });
  chart.setOption(option);
  const svg = chart.renderToSVGString();

  const texts = parseTexts(svg);
  const legendItems = texts.filter(t => t.fill === '#5f6e85');
  const pctLabels = texts.filter(t => t.fill === '#61708a');
  const arc = firstArc(svg);
  const minWH = Math.min(width, CHART_HEIGHT);
  const expectCx = parseFloat(option.series[0].center[0]) / 100 * width;
  const expectCy = parseFloat(option.series[0].center[1]) / 100 * CHART_HEIGHT;
  const expectOuter = parseFloat(option.series[0].radius[1]) / 100 * minWH / 2;

  console.log(`  图例 orient=${option.legend.orient} type=${option.legend.type} 条目=${legendItems.length}；环形图 ${JSON.stringify(option.series[0].center)} / ${JSON.stringify(option.series[0].radius)} → 中心(${r1(expectCx)},${r1(expectCy)}) 外半径 ${r1(expectOuter)}`);

  assertOk(!!arc, 'ECharts 画出了环形图弧线');
  assertOk(!!arc && Math.abs(arc.cx - expectCx) < 0.6 && Math.abs(arc.cy - expectCy) < 0.6 && Math.abs(arc.outer - expectOuter) < 0.6,
    '弧线圆心/半径与 option 声明一致', arc ? `实测(${r1(arc.cx)},${r1(arc.cy)}) r=${r1(arc.outer)}` : '');

  const data = option.series[0].data;
  const nonzero = sample.categories.filter(c => Number(c.count) > 0).length;
  assertOk(!data.some(d => d.value === 0), '0 值类别未进入环形图');
  assertOk(legendItems.length === nonzero, '图例条目数 = 非 0 类别数', `${legendItems.length} vs ${nonzero}`);

  if (legendItems.length) {
    const legendBox = bounds(legendItems);
    const graphicBox = bounds([...polylinePoints(svg), ...pctLabels, { left: arc.cx - arc.outer, right: arc.cx + arc.outer, top: arc.cy - arc.outer, bottom: arc.cy + arc.outer }]);
    assertOk(!intersects(legendBox, graphicBox), '图例与图形/标签不重叠',
      `图例 x[${r1(legendBox.left)},${r1(legendBox.right)}] y[${r1(legendBox.top)},${r1(legendBox.bottom)}]；图形 x[${r1(graphicBox.left)},${r1(graphicBox.right)}] y[${r1(graphicBox.top)},${r1(graphicBox.bottom)}]`);
    const all = bounds([...texts.map(t => ({ left: t.left, right: t.right, top: t.top, bottom: t.bottom })), { left: arc.cx - arc.outer, right: arc.cx + arc.outer, top: arc.cy - arc.outer, bottom: arc.cy + arc.outer }]);
    assertOk(all.left >= -1 && all.right <= width + 1 && all.top >= -1 && all.bottom <= CHART_HEIGHT + 1, '绘制内容未被画布裁掉',
      `内容范围 x[${r1(all.left)},${r1(all.right)}] y[${r1(all.top)},${r1(all.bottom)}]`);
  }
  if (note) console.log(`  图下说明：${note}`);
}

console.log(`环形图布局自检：${widths.length} 种容器宽度，样例 ${sample.categories.length} 个类别（其中 ${sample.categories.filter(c => !Number(c.count)).length} 个为 0）`);
widths.forEach(checkWidth);

console.log('\n=== 容器被 x-show 隐藏（宽高为 0）===');
assertOk(!buildOption(0, CHART_HEIGHT).option, 'drawChart 在容器不可见时提前返回（不画 0×0）');

console.log(`\n${failures ? `失败 ${failures} 项` : '全部通过'}`);
process.exit(failures ? 1 : 0);
