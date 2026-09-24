// DOM 桩渲染测试：用最小 DOM 模拟真正跑一遍模板里的渲染函数，
// 确保 11 个页面（7 品种 + 4 价差）都能渲染出「头部 + 六图」，
// 并验证 canvas 画图不抛异常。
// 用法: node domtest.js
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'dashboard.html'), 'utf8');

/* ---------- 1. 抽出模板 script 里的渲染逻辑 ---------- */
// 取 <script> ... </script> 的内容，去掉末尾的启动调用（我们在桩里自己驱动）
const sIdx = html.lastIndexOf('<script>');
const eIdx = html.lastIndexOf('</script>');
let js = html.slice(sIdx + '<script>'.length, eIdx);
// 去掉「启动」段（需要真实 DOM 与 setInterval），我们只调 renderPage
js = js.replace(/\/\* 启动 \*\/[\s\S]*$/, '');
// 去掉 setInterval 定时器与 beforeunload 监听，避免桩环境报错
js = js.replace(/setInterval\(\(\)=>\{[\s\S]*?\},1000\);/, '');
js = js.replace(/window\.addEventListener\('beforeunload'[\s\S]*?\}\);/, '');
js = js.replace(/window\.addEventListener\('resize'[\s\S]*?\}\);/, '');

/* ---------- 2. 最小 DOM 桩 ---------- */
let canvasCount = 0, drawnCount = 0;
/* canvas 桩：innerHTML 里的 <canvas> 无法真解析，用代理对象代替，
   只要能接住 drawChart 的调用即可。 */
function mkCanvasProxy(owner) {
  canvasCount++;
  return {
    tagName: 'CANVAS',
    parentNode: owner,
    clientWidth: 360, clientHeight: 186,
    width: 0, height: 0,
    getContext() {
      return new Proxy({}, {
        get(t, p) {
          if (p === 'measureText') return () => ({ width: 10 });
          if (p === 'canvas') return null;
          return () => {};
        },
        set() { return true; }
      });
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
}

function mkEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    children: [], parentNode: null,
    className: '', innerHTML: '', textContent: '', dataset: {},
    style: {}, checked: false,
    _attrs: {},
    set className(v) { this._cls = v; },
    get className() { return this._cls || ''; },
    appendChild(c) {
      if (c && c.__frag) { c.children.forEach(x => this.appendChild(x)); return c; }
      c.parentNode = this; this.children.push(c); return c;
    },
    querySelector(sel) {
      if (sel === 'canvas') {
        // 同样按 innerHTML 里声明的 <canvas> 虚拟补出（每个 tfcard 恰好 1 个）
        if (/<canvas/.test(String(this.innerHTML))) return mkCanvasProxy(this);
        for (const c of this.children) {
          const r = c.querySelector('canvas');
          if (r) return r;
        }
        return null;
      }
      const want = sel.replace(/^\./, '');
      let found = null;
      const walk = n => {
        for (const c of n.children) {
          if (!found && want && (c._cls || '').split(/\s+/).includes(want)) { found = c; return; }
          walk(c); if (found) return;
        }
      };
      walk(this);
      return found;
    },
    querySelectorAll(sel) {
      // 真实 DOM 里 <canvas> 是在 innerHTML 串里写的，桩无法解析 HTML，
      // 所以这里对 canvas 做「按 innerHTML 里的 <canvas 出现次数虚拟补出」的处理：
      // 每个 .tfcard 都恰好含 1 个 <canvas>（见 renderTfCard 模板）。
      const want = sel.replace(/^\./, '');
      const out = [];
      const self = this;
      if (sel === 'canvas') {
        // 先把自身 innerHTML 里声明的 canvas 数补上
        const own = (String(self.innerHTML).match(/<canvas/g) || []).length;
        const walkC = n => {
          const c2 = (String(n.innerHTML).match(/<canvas/g) || []).length;
          if (c2) out.push(...Array.from({ length: c2 }, () => mkCanvasProxy(n)));
          n.children.forEach(walkC);
        };
        walkC(self);
        return out;
      }
      const walk = n => {
        for (const c of n.children) {
          const cls = (c._cls || '').split(/\s+/);
          if (cls.includes(want)) out.push(c);
          walk(c);
        }
      };
      walk(this);
      return out;
    },
    classList: {
      add() {}, remove() {}, toggle() {},
      contains() { return false; }
    },
    addEventListener() {},
    setAttribute(k, v) { this._attrs[k] = v; },
    getContext() {
      return new Proxy({}, {
        get(t, p) {
          if (p === 'canvas') return null;
          if (p === 'measureText') return () => ({ width: 10 });
          return () => {};
        },
        set() { return true; }
      });
    },
    get clientWidth() { return 360; },
    get clientHeight() { return 186; },
    scrollTo() {},
    focus() {},
  };
  if (el.tagName === 'CANVAS') canvasCount++;
  return el;
}
// innerHTML 赋值为纯文本即可（我们只关心结构与 canvas）
const origSet = Object.getOwnPropertyDescriptor(mkEl('div'), 'innerHTML');

const contentEl = mkEl('div'); contentEl.id = 'content';
const symbarEl = mkEl('div'); symbarEl.id = 'symbar';
const dataAtEl = mkEl('b'); dataAtEl.id = 'dataAt';
const genAtEl = mkEl('b'); genAtEl.id = 'genAt';
const cdEl = mkEl('span'); cdEl.id = 'cd';
const autoRefEl = mkEl('input'); autoRefEl.id = 'autoRef'; autoRefEl.checked = true;

const byId = {
  content: contentEl, symbar: symbarEl, dataAt: dataAtEl,
  genAt: genAtEl, cd: cdEl, autoRef: autoRefEl
};

let rafQueue = [];
global.window = {
  devicePixelRatio: 1,
  scrollTo() {},
  addEventListener() {},
  requestAnimationFrame(cb) { rafQueue.push(cb); },
};
global.document = {
  createElement: mkEl,
  getElementById(id) { return byId[id] || null; },
  querySelectorAll() { return []; },
  addEventListener() {},
};
global.sessionStorage = {
  _d: {},
  getItem(k) { return this._d[k] ?? null; },
  setItem(k, v) { this._d[k] = String(v); },
};

/* ---------- 3. 抽出 PAYLOAD 并注入 ---------- */
let ps = html.indexOf('let PAYLOAD = ');
const pjs = ps + 'let PAYLOAD = '.length;
let pe = -1;
for (const m of ['/* 六个图表周期', 'const TF_ORDER', 'const TF_NAME']) {
  const k = html.indexOf(m, pjs);
  if (k >= 0 && (pe < 0 || k < pe)) pe = k;
}
let rawJson = html.slice(pjs, pe).replace(/\s+$/, '');
if (rawJson.endsWith(';')) rawJson = rawJson.slice(0, -1);
const PAYLOAD = JSON.parse(rawJson);
js = js.replace('let PAYLOAD = /*__DATA__*/null;', 'let PAYLOAD = __PAYLOAD__;');
js = js.replace(/let PAYLOAD =[\s\S]*?;/, 'let PAYLOAD = __PAYLOAD__;');

/* ---------- 4. 执行 ---------- */
let errors = [];
try {
  const fn = new Function('__PAYLOAD__', js + '\nreturn { renderPage, renderEscalation, renderLongterm, tfOrder: TF_ORDER, drawChart };');
  const api = fn(PAYLOAD);
  console.log('=== DOM 桩渲染测试 ===');
  console.log('TF_ORDER =', JSON.stringify(api.tfOrder));

  const pages = PAYLOAD.pages || [];
  console.log(`待渲染页面: ${pages.length}\n`);

  pages.forEach((p, i) => {
    let el;
    try {
      el = api.renderPage(p);
    } catch (ex) {
      errors.push(`${p.code} renderPage 抛异常: ${ex.message}`);
      return;
    }
    const heads = el.querySelectorAll('symhead').length + el.querySelectorAll('iphead').length;
    const cards = el.querySelectorAll('tfcard');
    const canvases = el.querySelectorAll('canvas');
    const ltpanels = el.querySelectorAll('ltpanel');
    const escalations = el.querySelectorAll('escalation');
    const kindOk = p.kind === 'interprice'
      ? el.querySelectorAll('iphead').length === 1
      : el.querySelectorAll('symhead').length === 1;

    if (!kindOk) errors.push(`${p.code} 头部类型与 kind 不符`);
    if (cards.length !== 6) errors.push(`${p.code} tfcard 数 ${cards.length} != 6`);
    if (canvases.length !== 6) errors.push(`${p.code} canvas 数 ${canvases.length} != 6`);
    if (ltpanels.length !== 1) errors.push(`${p.code} 长周期面板数 ${ltpanels.length} != 1`);
    if (escalations.length !== 1) errors.push(`${p.code} 递进提示数 ${escalations.length} != 1`);

    // 逐张画图（模拟浏览器）
    cards.forEach((card, k) => {
      const tf = api.tfOrder[k];
      const bundle = (p.bars || {})[tf];
      if (!bundle) { errors.push(`${p.code} ${tf} 无图表数据`); return; }
      try {
        const cv = card.querySelector('canvas');
        api.drawChart(cv, bundle.bars, bundle.ind || null);
        drawnCount++;
      } catch (ex) {
        errors.push(`${p.code} ${tf} drawChart 抛异常: ${ex.message}`);
      }
    });

    console.log(`${String(i).padStart(2)} ${p.kind === 'interprice' ? '价差' : '品种'} ${String(p.code).padEnd(8)} ${String(p.name).padEnd(12)} 头=${heads} 图=${cards.length} 长周期=${ltpanels.length} 提示=${escalations.length}`);
  });

  // 跑一遍 rAF 队列（验证延迟重绘不抛异常）
  rafQueue.forEach(cb => { try { cb(0); } catch (e) { errors.push('rAF 回调异常: ' + e.message); } });

  console.log(`\n共创建 canvas ${canvasCount} 个，成功绘制 ${drawnCount} 次`);
} catch (ex) {
  console.error('FATAL: 执行模板脚本失败:', ex.message);
  console.error(ex.stack.split('\n').slice(0, 6).join('\n'));
  process.exit(1);
}

console.log('\n=== 错误检查 ===');
if (errors.length) {
  console.log('发现 ' + errors.length + ' 个问题:');
  errors.slice(0, 30).forEach(e => console.log('  - ' + e));
  process.exit(1);
} else {
  console.log('✅ 11 个页面全部渲染成功（头部 + 六图 + 长周期 + 递进提示）');
}
