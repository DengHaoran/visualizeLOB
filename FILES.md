# 文件清单 — visualizeLOB

本文件记录项目中所有文件(含代码、环境、示例数据)的详细信息,对应 PLAN.md 中
"边界框定"的要求:所有生成物都放在项目文件夹下,并在此 markdown 中登记。

---

## 1. 源代码

### `visualize_lob.py`
工具的全部实现,所有 class 与函数都集中在这一个文件里。按依赖顺序包含四个组件:

| 组件 | 类型 | 说明 |
| --- | --- | --- |
| `InternalOrderBook` | class | 内部限价撮合引擎,维护买卖双边的价格档位与时间优先队列,支持挂限价单(零/部分/全部成交)与撤单。**仅供 toy 数据生成使用,不属于对外 API。** |
| `generate_toy_data()` | function | 用 `InternalOrderBook` 模拟挂单/撤单/成交,生成逻辑自洽的 toy 数据并写入 parquet。 |
| `LOBDataLoader` | class | 读取两个 parquet 文件,支持按股票代码 / 时间区间 / adjIndex 区间过滤;核心方法 `get_frame(pos)`、`get_trigger(pos)`。 |
| `LOBVisualizer` | class | 渲染层。`plot_single_frame(pos)` 画静态单帧(留存/新增/减少三段堆叠柱 + 分割线);`plot_animation(...)` 生成带播放/暂停/滑块的 plotly 动画。 |

模块对外暴露 `generate_toy_data`、`LOBDataLoader`、`LOBVisualizer`(见 `__all__`)。
代码每行均配有中文注释。直接运行 `python visualize_lob.py` 会生成一份默认 toy 数据。

### `demo.ipynb`
JupyterLab 演示 Notebook,串起完整工作流:生成 toy 数据 → 读入 → 静态单帧 → 动态回放 → 过滤后回放。

### `test_visualize_lob.py`
冒烟测试与逻辑自洽性校验脚本,无需 pytest,直接 `uv run python test_visualize_lob.py` 即可运行。
覆盖四部分:撮合引擎(价格/时间优先与撤单)、`generate_toy_data` 的 schema 与逻辑自洽性、
`LOBDataLoader` 的读取与过滤、`LOBVisualizer` 的出图结构。全部通过退出码为 0,否则为 1。
测试数据写入系统临时目录并在结束后清理,不影响项目下的 `toy_data/`。

---

## 2. 环境配置

| 文件 | 说明 |
| --- | --- |
| `pyproject.toml` | uv 项目配置,声明依赖:pandas、numpy、scipy、plotly、matplotlib、pyarrow、jupyterlab、nbformat。 |
| `.python-version` | 锁定 Python 版本为 3.12。 |
| `uv.lock` | uv 生成的依赖锁定文件,保证环境可复现。 |
| `.venv/` | 虚拟环境目录(由 `uv sync` 生成,已在 `.gitignore` 中忽略)。 |

环境重建命令:`uv sync`。

---

## 3. 示例数据(toy_data/)

由 `generate_toy_data()` 生成,默认参数:`n_events=100`、`code=600519`、`seed=42`。
该目录已在 `.gitignore` 中忽略,可随时重新生成。

### `toy_data/orderbook.parquet`
订单簿逐帧快照,共 **101 行**(1 个初始帧 + 100 个变化帧)。仅记录前 10 档真正发生变化的帧。

| 列名 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int64 | 股票代码。 |
| `adjIndex` | int64 | 帧索引。`(code, adjIndex)` 双元组唯一定位一帧;单调递增但允许跳号。 |
| `time` | datetime | 交易所时间。 |
| `serverTime` | datetime | 本地收到行情的时间(略晚于 `time`)。 |
| `bidPx1`~`bidPx10` | float | 买盘 1~10 档价格。 |
| `bidVlm1`~`bidVlm10` | int | 买盘 1~10 档挂单量。 |
| `askPx1`~`askPx10` | float | 卖盘 1~10 档价格。 |
| `askVlm1`~`askVlm10` | int | 卖盘 1~10 档挂单量。 |

### `toy_data/triggerInfo.parquet`
引起订单簿变化的行情,共 **100 行**,与 orderbook 的第 1~100 帧一一对应(初始帧无触发)。

| 列名 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int64 | 股票代码。 |
| `adjIndex` | int64 | 引起该帧变化的行情索引,可经 `(code, adjIndex)` merge 到 orderbook。 |
| `triggerType` | str | `"order"`(挂限价单,含引发成交者)或 `"cancel"`(撤单)。 |

### 数据的逻辑自洽性
toy 数据由真实撮合引擎逐事件模拟产生,经校验满足:
- 每帧买价严格递减、卖价严格递增,买一始终低于卖一(无交叉盘);
- 任一事件下,相邻两帧中"同一价格挂单量上升"的价格至多 1 个(限价单只落在单一价位);
- `cancel` 事件不会带来任何价位的挂单量上升;
- `adjIndex` 单调递增且存在跳号。

---

## 4. 文档

| 文件 | 说明 |
| --- | --- |
| `README.md` | 项目简介与详细使用说明(环境激活、运行、测试等)。 |
| `PLAN.md` | 原始需求与里程碑。 |
| `FILES.md` | 本文件,项目文件清单。 |
| `.gitignore` | Git 忽略规则(忽略 `.venv/`、`__pycache__/`、`toy_data/` 等可重建产物)。 |

---

## 使用流程

```powershell
uv sync                  # 1. 安装依赖
jupyter lab demo.ipynb   # 2. 打开演示 Notebook,逐格运行
```

或在 Python 中直接调用:

```python
import visualize_lob as vl

vl.generate_toy_data()                                   # 生成 toy 数据
loader = vl.LOBDataLoader('toy_data/orderbook.parquet',
                          'toy_data/triggerInfo.parquet')  # 读入数据
viz = vl.LOBVisualizer(loader)                           # 创建可视化器
viz.plot_single_frame(8).show()                          # 查看静态单帧
viz.plot_animation().show()                              # 动态回放
```
