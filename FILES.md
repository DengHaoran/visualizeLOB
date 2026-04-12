# 项目文件清单 — visualizeLOB

本文件记录项目中所有生成的文件及其用途。

---

## 项目结构

```
visualizeLOB/
├── PLAN.md                          # 项目计划文档（原始需求）
├── README.md                        # 项目说明
├── FILES.md                         # 本文件 — 全部生成文件的详细清单
├── pyproject.toml                   # uv 项目配置（依赖声明）
├── .python-version                  # Python 版本锁定（3.12）
├── .gitignore                       # Git 忽略规则
├── visualize_lob.py                 # 核心模块（全部 class 和函数）
├── demo.ipynb                       # JupyterLab 演示 notebook
├── toy_data/                        # 生成的 toy 数据目录
│   ├── orderbook.parquet            # 订单簿快照数据
│   └── triggerInfo.parquet          # 触发信息数据
└── .venv/                           # uv 虚拟环境（不提交）
```

---

## 文件详细说明

### `visualize_lob.py` — 核心模块

包含全部代码逻辑，每行均有详细中文注释。

| 组件 | 类型 | 说明 |
|------|------|------|
| `InternalOrderBook` | class | 内部订单簿模拟引擎。维护买/卖盘字典，支持限价单提交（含价格优先撮合）和撤单。仅用于 toy 数据生成。 |
| `generate_toy_data()` | function | 生成 toy 数据。初始化 10 档订单簿后随机产生事件（被动挂单 25%+25%、主动吃单 10%+10%、撤单 15%+15%），仅记录 Top-10 快照实际变化的帧。输出两个 parquet 文件，并保证 `code` 为整型、`adjIndex` 单调递增但允许跳号。 |
| `LOBDataLoader` | class | 数据加载器。读取 parquet，支持按 code / start_time / end_time / start_index / end_index 筛选，提供 `get_frame(pos)` 和 `get_trigger(pos)` 接口。 |
| `LOBVisualizer` | class | 可视化器。`plot_single_frame(pos)` 生成堆叠式静态柱状图（base + delta + 分割线），`plot_animation(...)` 生成带播放/暂停/滑块和 triggerType 标签的逐帧动画。 |

### `demo.ipynb` — 演示 Notebook

| Cell | 内容 |
|------|------|
| 1 | Markdown：标题与整体说明 |
| 2-3 | 导入依赖 |
| 4-5 | 生成 toy 数据 |
| 6-7 | 加载数据、打印概况 |
| 8-9 | 单帧静态可视化（第 10 帧，含差异颜色） |
| 10-11 | 前 50 帧动态回放动画 |
| 12-13 | 按 adjIndex 20~40 筛选后的动画 |

### `toy_data/orderbook.parquet` — 订单簿快照

| 列名 | 类型 | 说明 |
|------|------|------|
| `code` | int | 股票代码，数据层面为整型；展示时可格式化为 6 位（如 `000001`） |
| `adjIndex` | int | 帧索引，(code, adjIndex) 唯一定位一帧；仅保证单调递增，不保证连续 |
| `time` | datetime | 交易所时间戳 |
| `serverTime` | datetime | 本地接收时间戳 |
| `bidPx1`~`bidPx10` | float | 买盘 1~10 档价格 |
| `bidVlm1`~`bidVlm10` | int | 买盘 1~10 档挂单量 |
| `askPx1`~`askPx10` | float | 卖盘 1~10 档价格 |
| `askVlm1`~`askVlm10` | int | 卖盘 1~10 档挂单量 |

共 101 帧（第 0 帧为初始状态 + 100 个事件帧）。

### `toy_data/triggerInfo.parquet` — 触发信息

| 列名 | 类型 | 说明 |
|------|------|------|
| `code` | int | 股票代码 |
| `adjIndex` | int | 对应帧索引（可通过 (code, adjIndex) merge 到 orderbook），允许跳号 |
| `triggerType` | str | "order"（限价单）或 "cancel"（撤单） |

共 100 条（adjIndex 0 为初始状态无触发）。

### `pyproject.toml` — 项目配置

由 `uv init` 生成，声明的依赖：
- pandas, numpy, scipy, plotly, matplotlib, jupyterlab, pyarrow

### `.python-version`

锁定 Python 3.12。

---

## 颜色方案

| 场景 | 颜色 | RGBA |
|------|------|------|
| 买盘-无变化 | 蓝色 | rgba(55, 128, 191, 0.70) |
| 买盘-量增加 | 深蓝 | rgba(0, 50, 150, 0.95) |
| 买盘-量减少 | 浅蓝 | rgba(150, 200, 240, 0.50) |
| 卖盘-无变化 | 红色 | rgba(219, 64, 82, 0.70) |
| 卖盘-量增加 | 深红 | rgba(160, 0, 20, 0.95) |
| 卖盘-量减少 | 浅红 | rgba(255, 170, 170, 0.50) |
