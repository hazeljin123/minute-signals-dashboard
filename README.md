# 分钟线买卖点信号看板

7 个期货品种 + 4 个跨品种价差的分钟线信号看板，
外加一个 **P2701-P2705 月差专属页**。

数据源为新浪财经公开行情接口（免费、无需鉴权），零第三方依赖，
只用 Python 标准库 + 系统 `curl`。

## 在线看板（手机 / 任何电脑）

**主看板**（11 页：7 品种 + 4 价差）：

```
https://hazeljin123.github.io/minute-signals-dashboard/
```

**月差专属页**（1 页：P2701-P2705）：

```
https://hazeljin123.github.io/minute-signals-dashboard/spread.html
```

两条网址由 GitHub Actions 在交易日自动更新（早盘 / 午盘 / 夜盘时段，
约每 4 分钟一次），且**同一次运行会同时更新两个页面**。
页面本身每 10 秒会去拉一次最新数据文件，所以打开后放着不动也会自动刷新。

> **实测结论**：GitHub Actions 的机器可以正常访问新浪行情接口
> （2026-09-24 实测首次运行 37 秒完成，全流程通过）。

> 数据新鲜度说明：云端靠 GitHub 的定时任务驱动，而免费 runner 会排队，
> 实际间隔通常 3-10 分钟浮动。**这不是实时行情**，适合做趋势判断。
> 需要秒级实时，请在本机跑下面的实时版。

## 本机实时版（真正的 10 秒刷新）

```bash
python serve.py                # 默认端口 8765，每 10 秒刷新
python serve.py --interval 5   # 改成每 5 秒
```

然后打开 `http://127.0.0.1:8765/dashboard.html`。

Windows 下也可以直接双击 `启动看板.bat`。

实时版会常驻抓取并重写 `dashboard.html`，只要你开着机就是真·实时。

## 直接生成一份静态看板

不想起服务，只想生成当前快照：

```bash
python build_dashboard.py      # 主看板 -> dashboard.html + dashboard.data.json
python build_spread.py         # 月差页 -> spread.html + spread.data.json
```

产出文件：

| 文件 | 用途 |
| --- | --- |
| `dashboard.html` | 主看板，自包含（数据已内嵌），双击即可打开 |
| `dashboard.data.json` | 主看板纯数据，供页面热更新用 |
| `spread.html` | 月差页，自包含，双击即可打开 |
| `spread.data.json` | 月差页纯数据，供页面热更新用 |

## 看板结构

**主看板 11 页**：7 个品种页 + 4 个跨品种价差页。

- 品种：棕榈油、豆粕、菜粕、豆油、菜油、鸡蛋、生猪
- 价差：豆油−菜油、豆粕−菜粕、豆油−棕榈、菜油−棕榈

**月差专属页 1 页**：P2701-P2705 月差（近月 − 远月）。

> 为什么单独开一页？P2705 于 2026-05 才上市，周线只有 19 根、月线只有 5 根，
> 比主看板的品种少很多。专属页把长周期门槛下调（日线 ≥20 根、周月线 ≥4 根），
> **能画就画**，避免整块数据因为门槛不够而消失。

每个页面展示 **6 张图**：1 分钟 / 5 分钟 / 15 分钟 / 日线 / 周线 / 月线。

页面顶部给出综合判断，长周期（日/周/月）单独做一段总结；
价差页的长周期分析的是**价差本身**，而不是两条腿各自的走势。

## 项目文件

| 文件 | 作用 |
| --- | --- |
| `fetch_data.py` | 数据抓取层（新浪接口、并发抓取、周月线聚合） |
| `indicators.py` | 技术指标（MA / RSI / MACD / ATR / 布林） |
| `patterns.py` | K 线形态识别 |
| `signal_engine.py` | 多周期信号合成、递进提示、止损止盈 |
| `longterm.py` | 日/周/月线长周期总结 |
| `interprice.py` | 跨品种价差 / 跨期月差计算与分析 |
| `build_dashboard.py` | 组装主看板数据包、生成 HTML |
| `template.html` | 主看板前端模板（含画图与热更新逻辑） |
| `fetch_spread.py` | 月差数据抓取层（P2701 / P2705 两条腿） |
| `build_spread.py` | 月差页生成器（单页、低门槛） |
| `template_spread.html` | 月差页前端模板 |
| `trading_calendar.py` | 交易日历（节假日、长假前夜无夜盘判断） |
| `serve.py` | 常驻抓取 + 本地 HTTP 服务（实时版） |
| `smoketest.js` | 数据完整性回归测试 |
| `domtest.js` | DOM 桩渲染测试（`node domtest.js spread.html` 可测月差页） |
| `wenhua_formula.txt` | 文华财经公式版本 |
| `使用说明.md` | 详细使用与设计说明 |

## 自动更新是怎么跑的

`.github/workflows/update-dashboard.yml`：

1. 定时触发（交易日早/午/夜盘时段）
2. **交易日检查**——周末、法定节假日、长假前夜直接跳过，不空跑
3. 先探一次新浪接口——**不通就立即失败**并说明原因
4. 依次跑 `build_dashboard.py`、`build_spread.py`
5. 校验产物（主看板 11 页 + 月差页 1 页、图表完整性）
6. 把两个页面一起发布到 GitHub Pages

时区注意：GitHub Actions 的 cron 按 **UTC** 计，工作流注释里已标注
换算后的北京时间时段。程序内部时间戳统一走东八区（见 `fetch_data.now_cn`）。

## 免责声明

信号由技术指标自动合成，仅供参考，**不构成任何投资建议**。
期货交易风险很大，请独立判断、自负盈亏。
