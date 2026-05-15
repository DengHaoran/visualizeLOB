# visualizeLOB

订单簿（Limit Order Book, LOB）动态可视化工具。

以 Python + Plotly 为工具，回放并动画展示买卖挂单（10 档以内深度）随时间逐帧变化的过程，
面向中国 A 股市场数据。深色表示挂单量增加（挂单 / 部分成交剩余），浅色表示减少（撤单 / 成交），
蓝色代表买盘，红色代表卖盘。

---

## 目录

- [功能特性](#功能特性)
- [环境要求](#环境要求)
- [安装与激活环境](#安装与激活环境)
- [快速开始](#快速开始)
- [运行演示 Notebook](#运行演示-notebook)
- [API 使用说明](#api-使用说明)
- [测试](#测试)
- [数据格式](#数据格式)
- [项目结构](#项目结构)

---

## 功能特性

- **toy 数据生成**：内置撮合引擎模拟挂单 / 撤单 / 成交，生成逻辑自洽的订单簿数据。
- **数据读取与过滤**：按股票代码、时间区间、`adjIndex` 区间筛选。
- **静态单帧可视化**：柱状图展示某一帧的盘口深度，并叠加与上一帧的差异（分割线 + 深浅配色）。
- **动态回放**：带播放 / 暂停按钮与时间滑块的交互式 Plotly 动画，逐帧展示订单簿演化。

---

## 环境要求

- **操作系统**：Windows / macOS / Linux 均可（开发环境为 Windows + PowerShell）。
- **Python**：3.12（由 `.python-version` 锁定）。
- **包管理器**：[`uv`](https://docs.astral.sh/uv/)。若未安装，可执行：

  ```powershell
  # Windows (PowerShell)
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

---

## 安装与激活环境

本项目用 `uv` 管理虚拟环境与依赖。**首次使用**只需在项目根目录执行：

```powershell
uv sync
```

该命令会：

1. 按 `.python-version` 准备 Python 3.12；
2. 在项目下创建 `.venv/` 虚拟环境；
3. 按 `uv.lock` 安装全部依赖（pandas、numpy、scipy、plotly、matplotlib、pyarrow、jupyterlab 等）。

之后有两种使用环境的方式：

**方式一（推荐）：用 `uv run` 免激活直接运行**，每条命令前加 `uv run` 即可：

```powershell
uv run python visualize_lob.py
```

**方式二：手动激活虚拟环境**，激活后当前终端的 `python` 即指向项目环境：

```powershell
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Windows (cmd)
.\.venv\Scripts\activate.bat

# macOS / Linux (bash/zsh)
source .venv/bin/activate
```

退出环境执行 `deactivate`。

---

## 快速开始

```powershell
# 1. 安装依赖（首次）
uv sync

# 2. 生成 toy 数据（在 toy_data/ 下生成两个 parquet 文件）
uv run python visualize_lob.py

# 3. 运行测试，确认一切正常
uv run python test_visualize_lob.py

# 4. 打开演示 Notebook
uv run jupyter lab demo.ipynb
```

---

## 运行演示 Notebook

`demo.ipynb` 串起完整工作流（生成数据 → 读入 → 静态单帧 → 动态回放 → 过滤后回放）。

```powershell
uv run jupyter lab demo.ipynb
```

在 JupyterLab 中自上而下逐格运行即可。Notebook 的第一格会自动生成 toy 数据，因此**无需**预先手动生成。

---

## API 使用说明

工具的全部实现都在单个模块 [`visualize_lob.py`](visualize_lob.py) 中，对外暴露三个名字：
`generate_toy_data`、`LOBDataLoader`、`LOBVisualizer`。

```python
import visualize_lob as vl

# ① 生成 toy 数据（写入 toy_data/orderbook.parquet 与 triggerInfo.parquet）
vl.generate_toy_data(out_dir="toy_data", n_events=100, code=600519, seed=42)

# ② 读入数据
loader = vl.LOBDataLoader("toy_data/orderbook.parquet",
                          "toy_data/triggerInfo.parquet")
print("总帧数:", len(loader))

# ③（可选）按 adjIndex / 时间 / 股票代码区间过滤
loader.filter(start_index=10, end_index=80)   # 链式调用，返回 loader 自身
loader.filter()                                # 不带参数 = 还原为全部数据

# ④ 创建可视化器
viz = vl.LOBVisualizer(loader)

# ⑤ 静态单帧：查看第 8 帧及其与上一帧的差异
viz.plot_single_frame(8).show()

# ⑥ 动态回放：带播放/暂停/滑块的交互动画
viz.plot_animation().show()
viz.plot_animation(start=10, end=40).show()    # 也可只回放某一段
```

主要接口一览：

| 接口 | 说明 |
| --- | --- |
| `generate_toy_data(out_dir, n_events, code, seed)` | 生成逻辑自洽的 toy 数据并写入 parquet。 |
| `LOBDataLoader(orderbook_path, triggerinfo_path)` | 读取两个 parquet 文件。 |
| `LOBDataLoader.filter(code, start_time, end_time, start_index, end_index)` | 按条件过滤，返回自身以支持链式调用。 |
| `LOBDataLoader.get_frame(pos)` | 取第 `pos` 帧（0 起下标），返回一个 `pandas.Series`。 |
| `LOBDataLoader.get_trigger(pos)` | 取第 `pos` 帧的触发信息；初始帧返回 `None`。 |
| `LOBVisualizer(loader, tick=None)` | 创建可视化器，`tick` 缺省时自动推断。 |
| `LOBVisualizer.plot_single_frame(pos)` | 返回某一帧的静态柱状图 `Figure`。 |
| `LOBVisualizer.plot_animation(start, end, frame_duration, transition_duration)` | 返回逐帧动画 `Figure`。 |

---

## 测试

项目自带一个无需 pytest 的冒烟测试脚本 [`test_visualize_lob.py`](test_visualize_lob.py)，
覆盖撮合引擎、数据生成与逻辑自洽性、数据加载与过滤、可视化出图四部分：

```powershell
uv run python test_visualize_lob.py
```

全部通过时会打印 `全部测试通过, 共 N 项断言。` 并以退出码 `0` 结束；任一断言失败则打印失败原因并以退出码 `1` 结束。

> 测试会把 toy 数据写到系统临时目录并在结束后清理，**不会**影响项目下的 `toy_data/`。

如需端到端验证演示 Notebook 可正常执行：

```powershell
uv run jupyter nbconvert --to notebook --execute demo.ipynb --output _demo_executed.ipynb
```

---

## 数据格式

`(code, adjIndex)` 双元组是连接下面两张表的主键。

**orderbook.parquet** —— 订单簿逐帧快照：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int | 股票代码 |
| `adjIndex` | int | 帧索引，单调递增但**允许跳号** |
| `time` / `serverTime` | datetime | 交易所时间 / 本地收到时间 |
| `bidPx1`–`bidPx10` / `bidVlm1`–`bidVlm10` | float / int | 买盘 1–10 档价格 / 挂单量 |
| `askPx1`–`askPx10` / `askVlm1`–`askVlm10` | float / int | 卖盘 1–10 档价格 / 挂单量 |

**triggerInfo.parquet** —— 引起订单簿变化的行情：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int | 股票代码 |
| `adjIndex` | int | 引起该帧变化的行情索引 |
| `triggerType` | str | `"order"`（挂限价单，含引发成交者）或 `"cancel"`（撤单） |

---

## 项目结构

```
visualizeLOB/
├── visualize_lob.py        # 核心模块：撮合引擎 + 数据生成 + 加载器 + 可视化器
├── test_visualize_lob.py   # 冒烟测试与逻辑自洽性校验脚本
├── demo.ipynb              # 演示 Notebook
├── pyproject.toml          # uv 项目配置与依赖声明
├── .python-version         # Python 版本锁定（3.12）
├── uv.lock                 # 依赖锁定文件
├── toy_data/               # 生成的示例数据（可重建，已在 .gitignore 中忽略）
├── README.md               # 本文件
├── PLAN.md                 # 原始需求与里程碑
└── FILES.md                # 项目文件清单
```
