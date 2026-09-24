// 冒烟测试：校验统一 pages 结构（7 品种页 + 4 跨品种价差页）的数据完整性
// 用法: node smoketest.js
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'dashboard.html'), 'utf8');

// 提取 PAYLOAD —— 兼容 `const PAYLOAD = ` 与 `let PAYLOAD = ` 两种声明。
// 注意：模板在 Windows 上可能被写成 CRLF，所以不能死抠 "\n\n"，
// 要用「找到下一个语句起始」的思路并把结尾的 ; 与空白一起剥掉。
let start = html.indexOf('let PAYLOAD = ');
let jsonStart;
if (start >= 0) {
  jsonStart = start + 'let PAYLOAD = '.length;
} else {
  start = html.indexOf('const PAYLOAD = ');
  if (start < 0) { console.error('FAIL: 未找到 PAYLOAD 起始'); process.exit(1); }
  jsonStart = start + 'const PAYLOAD = '.length;
}
// 结束标记按优先级依次尝试
let jsonEnd = -1;
for (const marker of ['/* 六个图表周期', 'const TF_ORDER', 'const TF_NAME']) {
  const k = html.indexOf(marker, jsonStart);
  if (k >= 0 && (jsonEnd < 0 || k < jsonEnd)) jsonEnd = k;
}
if (jsonEnd < 0) { console.error('FAIL: 未找到 PAYLOAD 结束'); process.exit(1); }
let rawJson = html.slice(jsonStart, jsonEnd).replace(/\s+$/, '');
if (rawJson.endsWith(';')) rawJson = rawJson.slice(0, -1);
const PAYLOAD = JSON.parse(rawJson);

// 页面上应出现的六个周期（顺序即前端展示顺序）
const CHART_TFS = ['1', '5', '15', 'daily', 'weekly', 'monthly'];
const LONG_TFS = ['daily', 'weekly', 'monthly'];

let errors = [];
const chk = (cond, msg) => { if (!cond) errors.push(msg); };

console.log('=== 数据包检查 ===');
console.log('生成时间:', PAYLOAD.generated_at);
console.log('数据时间:', PAYLOAD.data_at);
chk(!PAYLOAD.symbols, '旧版 symbols 字段仍在，应为 pages');
console.log('页面数:', (PAYLOAD.pages || []).length);

const pages = PAYLOAD.pages || [];
const symPages = pages.filter(p => p.kind === 'symbol');
const ipPages = pages.filter(p => p.kind === 'interprice');
console.log(`  ├ 品种页: ${symPages.length}`);
console.log(`  └ 价差页: ${ipPages.length}`);
chk(symPages.length === 7, `品种页应为 7 个，实为 ${symPages.length}`);
chk(ipPages.length === 4, `价差页应为 4 个，实为 ${ipPages.length}`);

// 价差必须接在品种页之后（用户要求「接在最顶上的生猪的后面」）
const firstIpIdx = pages.findIndex(p => p.kind === 'interprice');
chk(firstIpIdx === symPages.length, '价差页未紧接在品种页之后');
if (symPages.length) {
  chk(symPages[symPages.length - 1].code === 'LH0',
      `品种页最后一个应为生猪 LH0，实为 ${symPages[symPages.length - 1].code}`);
}

for (const p of pages) {
  const tag = `${p.kind === 'interprice' ? '价差' : '品种'}/${p.code}`;

  // ---- 结构必需字段 ----
  for (const k of ['kind', 'code', 'name', 'verdict', 'level', 'total_score', 'bars', 'periods']) {
    if (p[k] === undefined) errors.push(`${tag} 缺字段 ${k}`);
  }
  // 价差页额外要求两腿信息
  if (p.kind === 'interprice') {
    for (const k of ['label', 'front', 'back', 'front_name', 'back_name', 'summary', 'current']) {
      if (p[k] === undefined) errors.push(`${tag} 价差页缺字段 ${k}`);
    }
  }

  // ---- 六图完整性：必须恰好是 1/5/15 + 日/周/月，且不能有 30 ----
  const tfKeys = Object.keys(p.bars || {});
  chk(!tfKeys.includes('30'), `${tag} bars 仍含 30 分钟，应已撤下`);
  const missing = CHART_TFS.filter(tf => !(p.bars || {})[tf]);
  if (missing.length) errors.push(`${tag} 缺图表周期: ${missing.join(',')}`);
  chk(tfKeys.length === 6, `${tag} 图表周期数应为 6，实为 ${tfKeys.length} (${tfKeys})`);

  // ---- 每张图：K 线高开低收关系 + 指标长度对齐 ----
  for (const tf of CHART_TFS) {
    const bundle = (p.bars || {})[tf];
    if (!bundle) continue;
    const bs = bundle.bars || [];
    if (!bs.length) { errors.push(`${tag} ${tf} 无 K 线`); continue; }
    for (const b of bs) {
      for (const k of ['t', 'o', 'h', 'l', 'c', 'v']) {
        if (b[k] === undefined) { errors.push(`${tag} ${tf} ${b.t} 缺字段 ${k}`); break; }
      }
      if (!(b.h >= b.l)) errors.push(`${tag} ${tf} ${b.t} h<l`);
      if (!(b.h >= b.o && b.h >= b.c)) errors.push(`${tag} ${tf} ${b.t} h 非最高`);
      if (!(b.l <= b.o && b.l <= b.c)) errors.push(`${tag} ${tf} ${b.t} l 非最低`);
    }
    for (const k of ['ma5', 'ma20', 'rsi']) {
      const arr = bundle.ind && bundle.ind[k];
      if (arr && arr.length !== bs.length) {
        errors.push(`${tag} ${tf} ind.${k} 长度 ${arr.length} != bars ${bs.length}`);
      }
    }
  }

  // ---- 长周期时间格式：日线及以上应是 "YY-MM-DD"（10 字符原串切掉世纪），不是 "MM-DD HH:MM"
  for (const tf of LONG_TFS) {
    const bundle = (p.bars || {})[tf];
    if (!bundle || !bundle.bars.length) continue;
    const t = bundle.bars[0].t;
    if (!/^\d{2}-\d{2}-\d{2}$/.test(t)) {
      errors.push(`${tag} ${tf} 时间格式异常（应 YY-MM-DD）: ${t}`);
    }
  }
  // 分钟线应是 "MM-DD HH:MM"
  for (const tf of ['1', '5', '15']) {
    const bundle = (p.bars || {})[tf];
    if (!bundle || !bundle.bars.length) continue;
    const t = bundle.bars[0].t;
    if (!/^\d{2}-\d{2} \d{2}:\d{2}$/.test(t)) {
      errors.push(`${tag} ${tf} 时间格式异常（应 MM-DD HH:MM）: ${t}`);
    }
  }

  // ---- 递进提示 ----
  const e = p.escalation;
  if (!e) {
    errors.push(`${tag} 缺 escalation`);
  } else {
    if (![0, 1, 2, 3].includes(e.level)) errors.push(`${tag} escalation.level 非法: ${e.level}`);
    if (e.level >= 1 && !e.side) errors.push(`${tag} level=${e.level} 但 side 为空`);
    if (e.level === 0 && e.side) errors.push(`${tag} level=0 但 side=${e.side}`);
  }

  // ---- 长周期面板 ----
  const lt = p.longterm;
  if (!lt || !Object.keys(lt).length) {
    errors.push(`${tag} 缺 longterm`);
  } else {
    for (const k of LONG_TFS) {
      if (!lt[k]) errors.push(`${tag} longterm.${k} 缺失`);
      else if (!lt[k].desc) errors.push(`${tag} longterm.${k}.desc 为空`);
    }
    if (!lt.summary) errors.push(`${tag} longterm.summary 为空`);
  }

  // ---- 结论与分数一致性（品种页与价差页口径一致）----
  const structOverride = !!p.conflict_note;
  if (!structOverride) {
    if (p.level === 2 && p.total_score < 7) errors.push(`${tag} 标称强多但分数 ${p.total_score}`);
    if (p.level === -2 && p.total_score > -7) errors.push(`${tag} 标称强空但分数 ${p.total_score}`);
    if (p.total_score > 0.01 && p.level < 0) errors.push(`${tag} 分为正但判空(${p.level})`);
    if (p.total_score < -0.01 && p.level > 0) errors.push(`${tag} 分为负但判多(${p.level})`);
  }

  // ---- 价差页专有校验 ----
  if (p.kind === 'interprice') {
    const ds = (p.structure || {}).dir_sign;
    const upWord = '走强', dnWord = '走弱';   // 当前只有跨品种价差
    if (p.level > 0 && (p.verdict || '').includes(dnWord)) errors.push(`${tag} 判多但文字含"${dnWord}"`);
    if (p.level < 0 && (p.verdict || '').includes(upWord)) errors.push(`${tag} 判空但文字含"${upWord}"`);

    // 措辞必须匹配 kind；排除"波动收敛"（波动率收缩，合法用法）
    const stripVol = t => String(t || '').replace(/波动收敛/g, '');
    if (/走扩|收敛/.test(stripVol(p.verdict))) errors.push(`${tag} 跨品种价差误用跨期措辞`);
    if (/走扩|收敛/.test(stripVol(p.summary))) errors.push(`${tag} 跨品种价差 summary 误用跨期措辞`);

    if (Math.abs(p.level) === 2 && ds === 0) errors.push(`${tag} 结构横盘却报强档`);
    if (p.level > 0 && ds < 0) errors.push(`${tag} 判多却结构向下`);
    if (p.level < 0 && ds > 0) errors.push(`${tag} 判空却结构向上`);
    if (ds !== 0 && /震荡/.test(p.verdict || '')) errors.push(`${tag} 结构有方向但结论仍是震荡`);

    // strength（配色依据）必须与 level 同号
    if (p.level > 0 && !(p.strength > 0)) errors.push(`${tag} level>0 但 strength=${p.strength} 非正，配色会反`);
    if (p.level < 0 && !(p.strength < 0)) errors.push(`${tag} level<0 但 strength=${p.strength} 非负，配色会反`);
    if (p.level === 0 && p.strength !== 0) errors.push(`${tag} level=0 但 strength=${p.strength} 不为 0`);

    // 多空周期数须等于分周期评分方向数
    const perVals = Object.values(p.periods || {});
    const pPos = perVals.filter(v => v.raw_score > 0).length;
    const pNeg = perVals.filter(v => v.raw_score < 0).length;
    if (p.pos_periods !== pPos) errors.push(`${tag} pos_periods=${p.pos_periods} 与实际 ${pPos} 不符`);
    if (p.neg_periods !== pNeg) errors.push(`${tag} neg_periods=${p.neg_periods} 与实际 ${pNeg} 不符`);

    // 路径效率理论上限 100%（曾算出 544%、769%）
    if ((p.structure || {}).eff > 100.5) errors.push(`${tag} 路径效率 ${p.structure.eff}% 超过上限`);
  }
}

// ---- 品种页不应再内嵌 interprices（已改为独立页）----
for (const p of symPages) {
  if (p.interprices) errors.push(`${p.code} 品种页仍内嵌 interprices，应已拆为独立页`);
}

// ---- 回归：日/周/月线必须包含「当天」，不能停在昨天 ----
// 背景：新浪 getDailyKLine 只返回已收盘日K，盘中不含当天那根。
// 曾经因此导致日/周/月线的图少一根、长周期总结停在昨天。
// fetch_data.merge_today_daily 用实时快照拼出当日日K后，这条才成立。
console.log('\n=== 日线新鲜度检查 ===');
const today = (() => {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
})();
// 数据包里的日期是 "YY-MM-DD"（模板截过），转成 YYYY-MM-DD 比较
const toFull = t => {
  const m = /^(\d{2})-(\d{2})-(\d{2})$/.exec(String(t || ''));
  return m ? `20${m[1]}-${m[2]}-${m[3]}` : String(t || '');
};
console.log('今天:', today, '| 生成时间:', PAYLOAD.generated_at);

for (const p of pages) {
  const db = ((p.bars || {}).daily || {}).bars || [];
  if (!db.length) { errors.push(`${p.code} 无日线数据`); continue; }
  const lastFull = toFull(db[db.length - 1].t);
  const fresh = lastFull >= today;
  if (!fresh) {
    errors.push(`${p.code} 日线末根 ${lastFull} 落后于今天 ${today}（盘中未补当日K）`);
  }
  const wb = ((p.bars || {}).weekly || {}).bars || [];
  const mb = ((p.bars || {}).monthly || {}).bars || [];
  const wLast = wb.length ? toFull(wb[wb.length - 1].t) : '-';
  const mLast = mb.length ? toFull(mb[mb.length - 1].t) : '-';
  console.log(`  ${String(p.code).padEnd(8)} 日线末=${lastFull} 周线末=${wLast} 月线末=${mLast} ${fresh ? 'OK' : 'STALE'}`);
  if (wb.length && wLast < today) errors.push(`${p.code} 周线末根 ${wLast} 未含本周`);
  if (mb.length && mLast < today) errors.push(`${p.code} 月线末根 ${mLast} 未含本月`);
}

console.log('\n=== 逐页摘要 ===');
for (const p of pages) {
  const sc = Object.entries(p.periods || {})
    .map(([k, v]) => `${k}m=${v.raw_score >= 0 ? '+' : ''}${v.raw_score}`).join(' ');
  const lvl = `L${p.level}`;
  console.log(`${p.kind === 'interprice' ? '价差' : '品种'} ${String(p.code).padEnd(8)} ${String(p.name).padEnd(12)} ${String(p.verdict).padEnd(18)} 分=${String(p.total_score).padStart(6)} ${lvl}`);

  const e = p.escalation || {};
  const sideTxt = e.side === 'long' ? '看涨' : e.side === 'short' ? '看跌' : '无';
  console.log(`     递进: L${e.level} ${sideTxt}  ${(e.text || '').slice(0, 46)}`);
  console.log(`     长周期: ${((p.longterm || {}).summary || '').slice(0, 66)}`);
  if (p.kind === 'interprice') {
    console.log(`     分周期: ${sc}`);
    if (p.conflict_note) console.log(`     说明: ${p.conflict_note}`);
  }
  const cnt = CHART_TFS.map(tf => `${tf}:${((p.bars || {})[tf] || { bars: [] }).bars.length}`).join(' ');
  console.log(`     六图根数: ${cnt}`);
}

console.log('\n=== 错误检查 ===');
if (errors.length) {
  console.log('发现 ' + errors.length + ' 个问题:');
  errors.slice(0, 30).forEach(e => console.log('  - ' + e));
  process.exit(1);
} else {
  console.log('✅ 统一 pages 结构校验全部通过');
}
