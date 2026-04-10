"""
visualize_lob.py — 订单簿（Limit Order Book, LOB）动态可视化工具

功能概述：
  1. 生成符合真实市场逻辑的 toy 数据（orderbook.parquet + triggerInfo.parquet）
  2. 加载并筛选订单簿数据（按股票代码 / 时间范围 / 索引范围）
  3. 单帧静态可视化（当前订单簿状态 + 与上一帧的差异高亮）
  4. 逐帧动态回放动画（含播放/暂停/滑块控制）

依赖：pandas, numpy, scipy, plotly, matplotlib, os
"""

# ==================== 导入部分 ====================

import os                                                          # 文件和目录操作
import numpy as np                                                 # 数值计算与随机数
import pandas as pd                                                # DataFrame 数据处理
import plotly.graph_objects as go                                  # Plotly 图形对象
from typing import Optional, Tuple, Dict, List                    # 类型注解

# ==================== 全局常量 ====================

MAX_DEPTH = 10                                                     # 订单簿最大显示档位数

# ---- 颜色方案 ----
# 买盘（Bid）使用蓝色系
BID_COLORS = {                                                     # 买盘颜色字典
    "normal":   "rgba(55, 128, 191, 0.70)",                       # 标准蓝：无变化
    "increase": "rgba(0, 50, 150, 0.95)",                         # 深蓝色：量增加（挂单/部分成交后余量）
    "decrease": "rgba(150, 200, 240, 0.50)",                      # 浅蓝色：量减少（撤单/被吃）
}
# 卖盘（Ask）使用红色系
ASK_COLORS = {                                                     # 卖盘颜色字典
    "normal":   "rgba(219, 64, 82, 0.70)",                        # 标准红：无变化
    "increase": "rgba(160, 0, 20, 0.95)",                         # 深红色：量增加
    "decrease": "rgba(255, 170, 170, 0.50)",                      # 浅红色：量减少
}


# ==================== 第一部分：订单簿内部模拟器（仅用于 toy 数据生成） ====================

class InternalOrderBook:
    """
    内部订单簿模拟引擎

    维护一个完整的双边（买/卖）限价订单簿，
    支持两种操作：提交限价单（含撮合逻辑）和撤单。
    撮合规则：价格优先（最优价先成交），时间优先（本模拟中简化为 FIFO 汇总量）。
    仅供 generate_toy_data() 调用，不直接对外暴露。
    """

    def __init__(
        self,
        code: str,                                                 # 股票代码
        init_mid: float,                                           # 初始中间价（买一卖一的中点偏移半 tick）
        tick: float = 0.01,                                        # 最小价格变动单位
        n_levels: int = 10,                                        # 初始每侧生成的档位数
    ):
        """初始化订单簿，在中间价两侧各生成 n_levels 档随机挂单"""
        self.code = code                                           # 保存股票代码
        self.tick = tick                                           # 保存最小变动价位
        self.bids: Dict[float, int] = {}                           # 买盘：{价格 → 挂单量}
        self.asks: Dict[float, int] = {}                           # 卖盘：{价格 → 挂单量}

        # ---- 初始化买盘（从 mid 向下逐档生成） ----
        for i in range(1, n_levels + 1):                           # 遍历 1 到 n_levels
            price = round(init_mid - i * tick, 2)                  # 计算该档价格
            self.bids[price] = int(np.random.randint(20, 80) * 10) # 随机量 200~790

        # ---- 初始化卖盘（从 mid 向上逐档生成） ----
        for i in range(1, n_levels + 1):                           # 遍历 1 到 n_levels
            price = round(init_mid + i * tick, 2)                  # 计算该档价格
            self.asks[price] = int(np.random.randint(20, 80) * 10) # 随机量 200~790

    # ---- 属性 ----

    @property
    def best_bid(self) -> Optional[float]:
        """当前买一价（最高买价），买盘为空时返回 None"""
        return max(self.bids.keys()) if self.bids else None        # 字典键最大值

    @property
    def best_ask(self) -> Optional[float]:
        """当前卖一价（最低卖价），卖盘为空时返回 None"""
        return min(self.asks.keys()) if self.asks else None        # 字典键最小值

    # ---- 快照 ----

    def snapshot(self) -> Dict:
        """
        获取当前 Top-10 快照

        返回字典包含 bidPx1~10, bidVlm1~10, askPx1~10, askVlm1~10。
        不足 10 档的位置以 NaN/0 填充。
        """
        sorted_bids = sorted(                                      # 买盘按价格降序（买一最大）
            self.bids.items(), key=lambda x: -x[0]
        )[:MAX_DEPTH]                                              # 取前 10 档
        sorted_asks = sorted(                                      # 卖盘按价格升序（卖一最小）
            self.asks.items(), key=lambda x: x[0]
        )[:MAX_DEPTH]                                              # 取前 10 档

        snap: Dict = {}                                            # 初始化快照字典
        for i in range(MAX_DEPTH):                                 # 遍历 0~9
            lv = i + 1                                             # 档位编号 1~10
            # 买盘
            if i < len(sorted_bids):                               # 有该档位
                snap[f"bidPx{lv}"] = sorted_bids[i][0]             # 买价
                snap[f"bidVlm{lv}"] = int(sorted_bids[i][1])       # 买量
            else:                                                  # 档位不足
                snap[f"bidPx{lv}"] = np.nan                        # 价格 NaN
                snap[f"bidVlm{lv}"] = 0                            # 量 0
            # 卖盘
            if i < len(sorted_asks):                               # 有该档位
                snap[f"askPx{lv}"] = sorted_asks[i][0]             # 卖价
                snap[f"askVlm{lv}"] = int(sorted_asks[i][1])       # 卖量
            else:                                                  # 档位不足
                snap[f"askPx{lv}"] = np.nan                        # 价格 NaN
                snap[f"askVlm{lv}"] = 0                            # 量 0

        return snap                                                # 返回完整快照

    # ---- 提交限价单（含撮合） ----

    def submit_order(self, side: str, price: float, quantity: int) -> str:
        """
        提交一笔限价单

        撮合规则：
          - 买单：与卖盘中 价格 ≤ 买价 的部分逐档撮合（价格优先）
          - 卖单：与买盘中 价格 ≥ 卖价 的部分逐档撮合（价格优先）
        撮合后的剩余量挂到对应侧。

        参数:
            side: "buy" 或 "sell"
            price: 限价
            quantity: 数量
        返回:
            "order"
        """
        price = round(price, 2)                                    # 标准化到分
        remaining = quantity                                       # 待成交量

        if side == "buy":                                          # ---- 买入 ----
            # 逐档吃卖盘，直到剩余量为 0 或没有可匹配的卖单
            while remaining > 0 and self.asks:                     # 还有剩余且卖盘非空
                ba = min(self.asks.keys())                         # 当前卖一价
                if ba > price:                                     # 卖一价已超过限价
                    break                                          # 无法继续撮合
                matched = min(remaining, self.asks[ba])            # 本次成交量
                self.asks[ba] -= matched                           # 卖盘该档减量
                remaining -= matched                               # 买方剩余减少
                if self.asks[ba] <= 0:                             # 该档卖盘耗尽
                    del self.asks[ba]                              # 移除该价位

            if remaining > 0:                                      # 还有未成交的量
                self.bids[price] = (                               # 挂到买盘
                    self.bids.get(price, 0) + remaining            # 累加到已有量
                )

        else:                                                      # ---- 卖出 ----
            # 逐档吃买盘
            while remaining > 0 and self.bids:                     # 还有剩余且买盘非空
                bb = max(self.bids.keys())                         # 当前买一价
                if bb < price:                                     # 买一价已低于限价
                    break                                          # 无法继续撮合
                matched = min(remaining, self.bids[bb])            # 本次成交量
                self.bids[bb] -= matched                           # 买盘该档减量
                remaining -= matched                               # 卖方剩余减少
                if self.bids[bb] <= 0:                             # 该档买盘耗尽
                    del self.bids[bb]                              # 移除该价位

            if remaining > 0:                                      # 还有未成交的量
                self.asks[price] = (                               # 挂到卖盘
                    self.asks.get(price, 0) + remaining            # 累加到已有量
                )

        return "order"                                             # 返回触发类型

    # ---- 撤单 ----

    def cancel_order(self, side: str, price: float, quantity: int) -> str:
        """
        撤销指定价位上的挂单

        参数:
            side: "buy" 或 "sell"
            price: 撤单价位
            quantity: 撤单数量（实际撤量不超过该价位现有量）
        返回:
            "cancel"
        """
        price = round(price, 2)                                    # 标准化到分

        if side == "buy" and price in self.bids:                   # 撤买盘
            cancel_qty = min(quantity, self.bids[price])           # 不超过现有量
            self.bids[price] -= cancel_qty                         # 减去撤单量
            if self.bids[price] <= 0:                              # 此价位清空
                del self.bids[price]                               # 移除价位

        elif side == "sell" and price in self.asks:                # 撤卖盘
            cancel_qty = min(quantity, self.asks[price])           # 不超过现有量
            self.asks[price] -= cancel_qty                         # 减去撤单量
            if self.asks[price] <= 0:                              # 此价位清空
                del self.asks[price]                               # 移除价位

        return "cancel"                                            # 返回触发类型


# ==================== Toy 数据生成函数 ====================

def generate_toy_data(
    output_dir: str,                                               # 输出目录路径
    code: str = "000001",                                          # 股票代码
    n_events: int = 100,                                           # 期望生成的事件帧数
    seed: int = 42,                                                # 随机种子（可复现）
) -> Tuple[str, str]:
    """
    生成符合真实市场逻辑的 toy 订单簿数据，保存为两个 parquet 文件。

    生成流程：
      1. 初始化一个 10 档深度的订单簿
      2. 随机产生"被动挂单 / 主动吃单 / 撤单"事件
      3. 每次事件后记录新的订单簿快照与触发信息
      4. 仅当 Top-10 快照实际发生变化时才记录新帧

    参数:
        output_dir: 输出目录
        code: 股票代码
        n_events: 最多尝试的事件数（实际记录帧数 ≤ n_events）
        seed: 随机种子
    返回:
        (orderbook_path, triggerInfo_path)
    """
    np.random.seed(seed)                                           # 固定随机种子
    os.makedirs(output_dir, exist_ok=True)                         # 建输出目录

    # ---- 创建模拟订单簿 ----
    ob = InternalOrderBook(                                        # 实例化模拟器
        code=code,                                                 # 股票代码
        init_mid=10.005,                                           # 中间价（使买一=10.00，卖一=10.01）
        tick=0.01,                                                 # 最小变动 0.01 元
        n_levels=10,                                               # 每侧 10 档
    )

    ob_records: List[Dict] = []                                    # 所有帧快照列表
    trigger_records: List[Dict] = []                               # 所有触发信息列表
    base_time = pd.Timestamp("2026-01-05 09:30:00")               # 基准交易时间

    # ---- 记录初始状态（adjIndex=0，无触发事件） ----
    snap = ob.snapshot()                                           # 获取初始快照
    snap["code"] = code                                            # 写入股票代码
    snap["adjIndex"] = 0                                           # 初始帧索引
    snap["time"] = base_time                                       # 交易所时间戳
    snap["serverTime"] = base_time + pd.Timedelta(microseconds=50) # 本地接收时间
    ob_records.append(snap)                                        # 加入记录
    adj_index = 0                                                  # 当前帧游标

    # ---- 事件概率分布 ----
    event_types = [                                                # 事件类型列表
        "buy_passive",                                             # 被动买入（不穿越 spread）
        "sell_passive",                                            # 被动卖出
        "buy_aggr",                                                # 主动买入（吃卖盘）
        "sell_aggr",                                               # 主动卖出（吃买盘）
        "cancel_buy",                                              # 撤买盘
        "cancel_sell",                                             # 撤卖盘
    ]
    event_probs = [0.25, 0.25, 0.10, 0.10, 0.15, 0.15]           # 对应概率

    # ---- 逐事件模拟 ----
    for i in range(1, n_events * 2 + 1):                           # 多尝试一些以确保有足够帧
        if adj_index >= n_events:                                  # 已达到目标帧数
            break                                                  # 停止生成

        time_now = base_time + pd.Timedelta(milliseconds=i * 100)  # 每 100ms 一个时间戳
        server_time = time_now + pd.Timedelta(                     # 本地接收延迟
            microseconds=int(np.random.randint(20, 200))           # 20~200 微秒
        )

        event = np.random.choice(event_types, p=event_probs)      # 随机选事件类型
        prev_snap = ob.snapshot()                                  # 事件前快照（用于比较）
        trigger_type: Optional[str] = None                         # 触发类型

        # ======== 处理各事件类型 ========

        if event == "buy_passive":                                 # ---- 被动买入 ----
            bb = ob.best_bid                                       # 当前买一价
            if bb is None:                                         # 买盘空则跳过
                continue
            offset = np.random.randint(0, 4)                       # 偏移 0~3 档
            price = round(bb - offset * ob.tick, 2)                # 在买一或以下挂单
            qty = int(np.random.randint(1, 20) * 10)               # 10~190 的量
            trigger_type = ob.submit_order("buy", price, qty)      # 提交买单

        elif event == "sell_passive":                               # ---- 被动卖出 ----
            ba = ob.best_ask                                       # 当前卖一价
            if ba is None:                                         # 卖盘空则跳过
                continue
            offset = np.random.randint(0, 4)                       # 偏移 0~3 档
            price = round(ba + offset * ob.tick, 2)                # 在卖一或以上挂单
            qty = int(np.random.randint(1, 20) * 10)               # 10~190 的量
            trigger_type = ob.submit_order("sell", price, qty)     # 提交卖单

        elif event == "buy_aggr":                                  # ---- 主动买入（成交） ----
            ba = ob.best_ask                                       # 卖一价
            if ba is None:                                         # 卖盘空则跳过
                continue
            offset = np.random.randint(0, 2)                       # 可能穿 0~1 档
            price = round(ba + offset * ob.tick, 2)                # 限价 ≥ 卖一
            qty = int(np.random.randint(5, 30) * 10)               # 50~290 的量
            trigger_type = ob.submit_order("buy", price, qty)      # 提交（触发撮合）

        elif event == "sell_aggr":                                 # ---- 主动卖出（成交） ----
            bb = ob.best_bid                                       # 买一价
            if bb is None:                                         # 买盘空则跳过
                continue
            offset = np.random.randint(0, 2)                       # 可能穿 0~1 档
            price = round(bb - offset * ob.tick, 2)                # 限价 ≤ 买一
            qty = int(np.random.randint(5, 30) * 10)               # 50~290 的量
            trigger_type = ob.submit_order("sell", price, qty)     # 提交（触发撮合）

        elif event == "cancel_buy":                                # ---- 撤买盘 ----
            if not ob.bids:                                        # 买盘空则跳过
                continue
            prices = sorted(ob.bids.keys(), reverse=True)          # 买盘价格降序
            idx = min(                                             # 从前 4 档中随机选
                np.random.randint(0, min(4, len(prices))),
                len(prices) - 1,
            )
            price = prices[idx]                                    # 选中的价位
            max_lots = max(1, ob.bids[price] // 10)                # 该价位最大手数
            qty = int(np.random.randint(1, max_lots + 1) * 10)    # 随机撤量
            trigger_type = ob.cancel_order("buy", price, qty)      # 执行撤单

        elif event == "cancel_sell":                               # ---- 撤卖盘 ----
            if not ob.asks:                                        # 卖盘空则跳过
                continue
            prices = sorted(ob.asks.keys())                        # 卖盘价格升序
            idx = min(                                             # 从前 4 档中随机选
                np.random.randint(0, min(4, len(prices))),
                len(prices) - 1,
            )
            price = prices[idx]                                    # 选中的价位
            max_lots = max(1, ob.asks[price] // 10)                # 该价位最大手数
            qty = int(np.random.randint(1, max_lots + 1) * 10)    # 随机撤量
            trigger_type = ob.cancel_order("sell", price, qty)     # 执行撤单

        # ======== 检查快照是否变化 ========
        new_snap = ob.snapshot()                                   # 事件后快照
        changed = False                                            # 默认未变化
        for key in new_snap:                                       # 逐字段比较
            old_val = prev_snap.get(key)                           # 旧值
            new_val = new_snap.get(key)                            # 新值
            if isinstance(old_val, float) and isinstance(new_val, float):  # 浮点数
                if pd.isna(old_val) and pd.isna(new_val):         # 都是 NaN
                    continue                                       # 视为相同
                if pd.isna(old_val) != pd.isna(new_val):          # NaN 状态不同
                    changed = True                                 # 有变化
                    break
                if abs(old_val - new_val) > 1e-9:                 # 数值差异
                    changed = True                                 # 有变化
                    break
            elif old_val != new_val:                               # 非浮点字段直接比较
                changed = True                                     # 有变化
                break

        if not changed or trigger_type is None:                    # 未发生实质变化
            continue                                               # 跳过该事件

        # ======== 记录新帧 ========
        adj_index += 1                                             # 帧索引递增
        new_snap["code"] = code                                    # 股票代码
        new_snap["adjIndex"] = adj_index                           # 帧索引
        new_snap["time"] = time_now                                # 交易所时间
        new_snap["serverTime"] = server_time                       # 本地时间
        ob_records.append(new_snap)                                # 加入快照记录

        trigger_records.append({                                   # 加入触发记录
            "code": code,                                          # 股票代码
            "adjIndex": adj_index,                                 # 对应帧索引
            "triggerType": trigger_type,                           # "order" 或 "cancel"
        })

    # ---- 保存为 parquet ----
    ob_df = pd.DataFrame(ob_records)                               # 快照 DataFrame
    trigger_df = pd.DataFrame(trigger_records)                     # 触发 DataFrame

    ob_path = os.path.join(output_dir, "orderbook.parquet")        # 订单簿路径
    trigger_path = os.path.join(output_dir, "triggerInfo.parquet")  # 触发信息路径

    ob_df.to_parquet(ob_path, index=False)                         # 写 parquet
    trigger_df.to_parquet(trigger_path, index=False)               # 写 parquet

    print(f"已生成 {len(ob_df)} 帧订单簿数据 → {ob_path}")          # 提示
    print(f"已生成 {len(trigger_df)} 条触发信息 → {trigger_path}")   # 提示

    return ob_path, trigger_path                                   # 返回路径元组


# ==================== 第二部分：数据加载器 ====================

class LOBDataLoader:
    """
    订单簿数据加载器

    负责读取 orderbook.parquet 与 triggerInfo.parquet，
    支持按股票代码 / 时间范围 / adjIndex 范围进行筛选，
    并提供逐帧访问接口。
    """

    def __init__(self, orderbook_path: str, trigger_path: str):
        """
        参数:
            orderbook_path: orderbook.parquet 文件路径
            trigger_path: triggerInfo.parquet 文件路径
        """
        self.ob_df = pd.read_parquet(orderbook_path)               # 读取订单簿
        self.trigger_df = pd.read_parquet(trigger_path)            # 读取触发信息
        self._filtered_ob = self.ob_df.copy()                      # 初始化为全部数据
        self._filtered_trigger = self.trigger_df.copy()            # 初始化为全部数据

    def filter(
        self,
        code: Optional[str] = None,                                # 股票代码
        start_time: Optional[str] = None,                          # 起始时间
        end_time: Optional[str] = None,                            # 结束时间
        start_index: Optional[int] = None,                         # 起始 adjIndex
        end_index: Optional[int] = None,                           # 结束 adjIndex（含）
    ) -> "LOBDataLoader":
        """
        筛选数据范围（支持链式调用）

        参数可任意组合，传 None 表示不限制该维度。
        """
        df = self.ob_df.copy()                                     # 从原始数据开始
        tdf = self.trigger_df.copy()                               # 触发表同步筛选

        if code is not None:                                       # 按股票代码
            df = df[df["code"] == code]                            # 筛选订单簿
            tdf = tdf[tdf["code"] == code]                         # 筛选触发信息

        if start_index is not None:                                # 按起始索引
            df = df[df["adjIndex"] >= start_index]                 # 筛选
            tdf = tdf[tdf["adjIndex"] >= start_index]              # 同步

        if end_index is not None:                                  # 按结束索引
            df = df[df["adjIndex"] <= end_index]                   # 筛选
            tdf = tdf[tdf["adjIndex"] <= end_index]                # 同步

        if start_time is not None:                                 # 按起始时间
            df = df[df["time"] >= pd.Timestamp(start_time)]        # 筛选
            valid_idx = set(df["adjIndex"].values)                 # 有效索引集合
            tdf = tdf[tdf["adjIndex"].isin(valid_idx)]             # 同步触发表

        if end_time is not None:                                   # 按结束时间
            df = df[df["time"] <= pd.Timestamp(end_time)]          # 筛选
            valid_idx = set(df["adjIndex"].values)                 # 有效索引集合
            tdf = tdf[tdf["adjIndex"].isin(valid_idx)]             # 同步触发表

        self._filtered_ob = df.reset_index(drop=True)              # 重置行索引
        self._filtered_trigger = tdf.reset_index(drop=True)        # 重置行索引
        return self                                                # 返回自身

    # ---- 属性 ----

    @property
    def n_frames(self) -> int:
        """筛选后的总帧数"""
        return len(self._filtered_ob)                              # 行数即帧数

    @property
    def indices(self) -> List[int]:
        """筛选后所有 adjIndex 值的列表"""
        return self._filtered_ob["adjIndex"].tolist()              # 转列表

    # ---- 帧访问 ----

    def get_frame(self, pos: int) -> pd.Series:
        """
        获取筛选结果中第 pos 个帧（0-based 位置）

        返回 pd.Series 包含该帧全部列。
        """
        return self._filtered_ob.iloc[pos]                         # 按位置取行

    def get_trigger(self, pos: int) -> Optional[str]:
        """
        获取筛选结果中第 pos 个帧的触发类型

        返回 "order" / "cancel"，初始帧（adjIndex=0）返回 None。
        """
        adj_index = self._filtered_ob.iloc[pos]["adjIndex"]        # 该帧的 adjIndex
        match = self._filtered_trigger[                            # 查找对应触发记录
            self._filtered_trigger["adjIndex"] == adj_index
        ]
        if len(match) > 0:                                         # 找到了
            return match.iloc[0]["triggerType"]                    # 返回类型
        return None                                                # 初始帧无触发


# ==================== 第三部分：可视化器 ====================

class LOBVisualizer:
    """
    订单簿可视化器

    基于 Plotly 构建交互式图表：
      - plot_single_frame(): 显示某一帧的订单簿状态（含前后帧差异高亮）
      - plot_animation(): 逐帧动态回放（含播放/暂停/帧滑块）
    """

    def __init__(self, loader: LOBDataLoader):
        """
        参数:
            loader: 已加载（并可选筛选）的 LOBDataLoader 实例
        """
        self.loader = loader                                       # 保存加载器引用

    # ---- 内部辅助方法 ----

    def _extract_levels(
        self, frame: pd.Series
    ) -> Tuple[List[float], List[int], List[float], List[int]]:
        """
        从一帧数据中提取买卖盘各档的价格与数量

        返回: (bid_prices, bid_volumes, ask_prices, ask_volumes)
              买价降序 → 升序翻转后传出，方便画图时从左到右
        """
        bid_px: List[float] = []                                   # 买价列表
        bid_vlm: List[int] = []                                    # 买量列表
        ask_px: List[float] = []                                   # 卖价列表
        ask_vlm: List[int] = []                                    # 卖量列表

        for lv in range(1, MAX_DEPTH + 1):                         # 遍历 10 档
            bp = frame.get(f"bidPx{lv}")                           # 买价
            bv = frame.get(f"bidVlm{lv}", 0)                      # 买量
            if pd.notna(bp) and bv > 0:                            # 有效
                bid_px.append(float(bp))                           # 加入
                bid_vlm.append(int(bv))                            # 加入

            ap = frame.get(f"askPx{lv}")                           # 卖价
            av = frame.get(f"askVlm{lv}", 0)                      # 卖量
            if pd.notna(ap) and av > 0:                            # 有效
                ask_px.append(float(ap))                           # 加入
                ask_vlm.append(int(av))                            # 加入

        # 买盘按升序排列（画图时从左到右价格递增）
        bid_pairs = sorted(zip(bid_px, bid_vlm), key=lambda x: x[0])  # 按价格升序
        if bid_pairs:                                              # 非空
            bid_px, bid_vlm = map(list, zip(*bid_pairs))           # 解压
        # 卖盘已按升序
        return bid_px, bid_vlm, ask_px, ask_vlm                   # 返回四列表

    def _price_to_vol_map(self, frame: pd.Series, side: str) -> Dict[float, int]:
        """
        构建 {价格: 数量} 映射，便于对比两帧差异

        参数:
            frame: 某一帧数据
            side: "bid" 或 "ask"
        """
        m: Dict[float, int] = {}                                   # 初始化空映射
        for lv in range(1, MAX_DEPTH + 1):                         # 10 档
            px = frame.get(f"{side}Px{lv}")                        # 价格
            vlm = frame.get(f"{side}Vlm{lv}", 0)                  # 数量
            if pd.notna(px) and vlm > 0:                           # 有效
                m[round(float(px), 2)] = int(vlm)                  # 加入映射
        return m                                                   # 返回

    def _build_stacked_frame_traces(
        self,
        pos: int,                                                  # 帧位置（0-based）
        all_labels: List[str],                                     # 全局统一价格标签
        show_legend: bool = True,                                  # 是否显示图例
    ) -> Tuple[List[go.Bar], str, List[Dict]]:
        """
        构建某一帧的堆叠式 Bar traces（base 不变量 + delta 变化量 + 分割线）

        设计思路：
          - 每个价位的柱子拆为两段堆叠：底部 base（不变量）+ 顶部 delta（变化量）
          - delta 段用 marker_line 画分割线，与 base 视觉区分
          - 量增加 → delta 深色   |  量减少 → delta 浅色/半透明（ghost，表示被移除的量）
          - 无变化 → 只有 base，delta=0
          - 同价格位置固定不动（x 轴用全局统一 categoryarray），变化在原地展示

        返回: (traces_list, title_text, annotations_for_trigger)
        """
        TRANSPARENT = "rgba(0,0,0,0)"                              # 透明色常量
        DIVIDER_COLOR = "rgba(40, 40, 40, 0.85)"                  # 分割线颜色（深灰）
        DIVIDER_WIDTH = 2.5                                        # 分割线宽度

        current = self.loader.get_frame(pos)                       # 当前帧数据

        # ---- 获取上一帧映射（计算差异） ----
        if pos > 0:                                                # 非首帧
            prev = self.loader.get_frame(pos - 1)                  # 上一帧
            prev_bid = self._price_to_vol_map(prev, "bid")         # 上帧买盘 {价格: 量}
            prev_ask = self._price_to_vol_map(prev, "ask")         # 上帧卖盘 {价格: 量}
        else:                                                      # 首帧
            prev_bid = {}                                          # 空映射
            prev_ask = {}                                          # 空映射

        cur_bid = self._price_to_vol_map(current, "bid")           # 当前帧买盘映射
        cur_ask = self._price_to_vol_map(current, "ask")           # 当前帧卖盘映射

        # ---- 为每个价格标签计算 base 和 delta ----
        bid_base_y: List[int] = []                                 # 买盘不变量
        bid_delta_y: List[int] = []                                # 买盘变化量
        bid_base_colors: List[str] = []                            # 买盘 base 颜色
        bid_delta_colors: List[str] = []                           # 买盘 delta 颜色
        bid_delta_line_colors: List[str] = []                      # 买盘分割线颜色
        bid_delta_texts: List[str] = []                            # 买盘变化标注
        bid_hover_texts: List[str] = []                            # 买盘 base 悬停

        ask_base_y: List[int] = []                                 # 卖盘不变量
        ask_delta_y: List[int] = []                                # 卖盘变化量
        ask_base_colors: List[str] = []                            # 卖盘 base 颜色
        ask_delta_colors: List[str] = []                           # 卖盘 delta 颜色
        ask_delta_line_colors: List[str] = []                      # 卖盘分割线颜色
        ask_delta_texts: List[str] = []                            # 卖盘变化标注
        ask_hover_texts: List[str] = []                            # 卖盘 base 悬停

        for label in all_labels:                                   # 遍历全局价格标签
            px = round(float(label), 2)                            # 转为浮点价格

            # ======== 买盘侧 ========
            cb = cur_bid.get(px, 0)                                # 当前帧该价位买量
            pb = prev_bid.get(px, 0)                               # 上一帧该价位买量

            if cb > 0 or pb > 0:                                   # 该价位存在买盘数据
                if cb > pb:                                        # 量增加（新挂单 / partial fill）
                    bid_base_y.append(pb)                          # base = 上一帧量
                    bid_delta_y.append(cb - pb)                    # delta = 增加量
                    bid_base_colors.append(                        # base 颜色
                        BID_COLORS["normal"] if pb > 0             # 有 base → 标准蓝
                        else TRANSPARENT                           # 无 base → 透明
                    )
                    bid_delta_colors.append(BID_COLORS["increase"])# delta 深蓝
                    bid_delta_line_colors.append(DIVIDER_COLOR)    # 分割线可见
                    bid_delta_texts.append(f"+{cb - pb}")          # "+N" 标注
                    bid_hover_texts.append(                        # 悬停文本
                        f"买盘 {label}<br>总量: {cb}<br>不变: {pb}<br>增加: +{cb-pb}"
                    )
                elif cb < pb:                                      # 量减少（撤单 / 被成交）
                    bid_base_y.append(cb)                          # base = 当前剩余量
                    bid_delta_y.append(pb - cb)                    # delta = 消失的量（ghost）
                    bid_base_colors.append(                        # base 颜色
                        BID_COLORS["normal"] if cb > 0             # 有剩余 → 标准蓝
                        else TRANSPARENT                           # 完全消失 → 透明
                    )
                    bid_delta_colors.append(BID_COLORS["decrease"])# delta 浅蓝（ghost）
                    bid_delta_line_colors.append(DIVIDER_COLOR)    # 分割线可见
                    bid_delta_texts.append(f"-{pb - cb}")          # "-N" 标注
                    bid_hover_texts.append(                        # 悬停文本
                        f"买盘 {label}<br>总量: {cb}<br>不变: {cb}<br>减少: -{pb-cb}"
                    )
                else:                                              # 无变化
                    bid_base_y.append(cb)                          # base = 全量
                    bid_delta_y.append(0)                          # delta = 0
                    bid_base_colors.append(BID_COLORS["normal"])   # 标准蓝
                    bid_delta_colors.append(TRANSPARENT)           # delta 透明
                    bid_delta_line_colors.append(TRANSPARENT)      # 分割线透明
                    bid_delta_texts.append("")                     # 无标注
                    bid_hover_texts.append(                        # 悬停文本
                        f"买盘 {label}<br>总量: {cb}<br>无变化"
                    )
            else:                                                  # 该价位无买盘数据
                bid_base_y.append(0)                               # 全 0
                bid_delta_y.append(0)
                bid_base_colors.append(TRANSPARENT)
                bid_delta_colors.append(TRANSPARENT)
                bid_delta_line_colors.append(TRANSPARENT)
                bid_delta_texts.append("")
                bid_hover_texts.append("")

            # ======== 卖盘侧 ========
            ca = cur_ask.get(px, 0)                                # 当前帧该价位卖量
            pa = prev_ask.get(px, 0)                               # 上一帧该价位卖量

            if ca > 0 or pa > 0:                                   # 该价位存在卖盘数据
                if ca > pa:                                        # 量增加
                    ask_base_y.append(pa)                          # base = 上一帧量
                    ask_delta_y.append(ca - pa)                    # delta = 增加量
                    ask_base_colors.append(
                        ASK_COLORS["normal"] if pa > 0
                        else TRANSPARENT
                    )
                    ask_delta_colors.append(ASK_COLORS["increase"])# delta 深红
                    ask_delta_line_colors.append(DIVIDER_COLOR)    # 分割线可见
                    ask_delta_texts.append(f"+{ca - pa}")
                    ask_hover_texts.append(
                        f"卖盘 {label}<br>总量: {ca}<br>不变: {pa}<br>增加: +{ca-pa}"
                    )
                elif ca < pa:                                      # 量减少
                    ask_base_y.append(ca)                          # base = 当前剩余
                    ask_delta_y.append(pa - ca)                    # delta = ghost
                    ask_base_colors.append(
                        ASK_COLORS["normal"] if ca > 0
                        else TRANSPARENT
                    )
                    ask_delta_colors.append(ASK_COLORS["decrease"])# delta 浅红
                    ask_delta_line_colors.append(DIVIDER_COLOR)    # 分割线可见
                    ask_delta_texts.append(f"-{pa - ca}")
                    ask_hover_texts.append(
                        f"卖盘 {label}<br>总量: {ca}<br>不变: {ca}<br>减少: -{pa-ca}"
                    )
                else:                                              # 无变化
                    ask_base_y.append(ca)
                    ask_delta_y.append(0)
                    ask_base_colors.append(ASK_COLORS["normal"])
                    ask_delta_colors.append(TRANSPARENT)
                    ask_delta_line_colors.append(TRANSPARENT)
                    ask_delta_texts.append("")
                    ask_hover_texts.append(
                        f"卖盘 {label}<br>总量: {ca}<br>无变化"
                    )
            else:                                                  # 该价位无卖盘数据
                ask_base_y.append(0)
                ask_delta_y.append(0)
                ask_base_colors.append(TRANSPARENT)
                ask_delta_colors.append(TRANSPARENT)
                ask_delta_line_colors.append(TRANSPARENT)
                ask_delta_texts.append("")
                ask_hover_texts.append("")

        # ---- 构建 4 个堆叠 traces（底→顶：bid_base, ask_base, bid_delta, ask_delta）----
        bid_base_trace = go.Bar(                                   # 买盘 base（不变部分）
            x=all_labels,                                          # 全局价格标签
            y=bid_base_y,                                          # 不变量
            marker_color=bid_base_colors,                          # 标准蓝 / 透明
            name="买盘 不变 (Bid)",                                 # 图例
            showlegend=show_legend,                                # 显示图例
            hovertext=bid_hover_texts,                             # 悬停详情
            hoverinfo="text",                                      # 使用自定义文本
        )

        ask_base_trace = go.Bar(                                   # 卖盘 base（不变部分）
            x=all_labels,
            y=ask_base_y,
            marker_color=ask_base_colors,
            name="卖盘 不变 (Ask)",
            showlegend=show_legend,
            hovertext=ask_hover_texts,
            hoverinfo="text",
        )

        bid_delta_trace = go.Bar(                                  # 买盘 delta（变化部分）
            x=all_labels,
            y=bid_delta_y,
            marker_color=bid_delta_colors,                         # 深蓝(增) / 浅蓝(减)
            marker_line=dict(                                      # 分割线
                width=DIVIDER_WIDTH,                               # 线宽
                color=bid_delta_line_colors,                       # 逐条控制
            ),
            text=bid_delta_texts,                                  # 变化量标注
            textposition="outside",                                # 标注在柱子外
            name="买盘 变化 (Bid Δ)",                              # 图例
            showlegend=show_legend,
            hovertext=bid_hover_texts,                             # 复用买盘悬停
            hoverinfo="text",
        )

        ask_delta_trace = go.Bar(                                  # 卖盘 delta（变化部分）
            x=all_labels,
            y=ask_delta_y,
            marker_color=ask_delta_colors,                         # 深红(增) / 浅红(减)
            marker_line=dict(                                      # 分割线
                width=DIVIDER_WIDTH,
                color=ask_delta_line_colors,
            ),
            text=ask_delta_texts,                                  # 变化量标注
            textposition="outside",
            name="卖盘 变化 (Ask Δ)",
            showlegend=show_legend,
            hovertext=ask_hover_texts,
            hoverinfo="text",
        )

        # ---- 标题与触发类型标注 ----
        adj_idx = int(current["adjIndex"])                         # 帧 adjIndex
        time_str = str(current["time"])                            # 时间字符串
        trigger = self.loader.get_trigger(pos)                     # 触发类型
        trigger_str = trigger if trigger else "初始状态"            # 显示文本

        title = (                                                  # 主标题
            f"订单簿 [{current['code']}]　|　"
            f"帧 #{adj_idx}　|　"
            f"时间 {time_str}"
        )

        # 醒目的触发类型标注框（order=蓝色标签，cancel=橙色标签）
        trigger_color = (                                          # 根据类型选色
            "#1a5276" if trigger == "order"                        # 蓝色：order
            else "#e67e22" if trigger == "cancel"                  # 橙色：cancel
            else "#7f8c8d"                                         # 灰色：初始状态
        )
        trigger_annotation = dict(                                 # 触发类型注释
            text=f"<b>  触发: {trigger_str.upper()}  </b>",        # 粗体 + 大写
            xref="paper", yref="paper",                            # 相对坐标
            x=0.98, y=1.06,                                        # 右上角
            xanchor="right", yanchor="bottom",
            showarrow=False,                                       # 无箭头
            font=dict(size=15, color="white"),                     # 白色字体
            bgcolor=trigger_color,                                 # 彩色背景
            bordercolor=trigger_color,                             # 同色边框
            borderwidth=1,                                         # 边框宽度
            borderpad=4,                                           # 内边距
        )

        # 颜色说明注释（固定在底部）
        legend_annotation = dict(                                  # 图例说明
            text=(
                "<b>图例</b>: 标准色=不变量　|　"
                "深色+分割线=新增量　|　"
                "浅色+分割线=减少量(ghost)"
            ),
            xref="paper", yref="paper",
            x=0.5, y=-0.12,
            xanchor="center",
            showarrow=False,
            font=dict(size=11, color="gray"),
        )

        annotations = [trigger_annotation, legend_annotation]      # 两个注释

        # 堆叠顺序：base 在底，delta 在顶（同侧买/卖不重叠因为价位互斥）
        traces = [                                                 # 4 traces 列表
            bid_base_trace,                                        # trace 0：买盘 base
            ask_base_trace,                                        # trace 1：卖盘 base
            bid_delta_trace,                                       # trace 2：买盘 delta
            ask_delta_trace,                                       # trace 3：卖盘 delta
        ]

        return traces, title, annotations                          # 返回

    def _get_all_price_labels(
        self, start_pos: int, end_pos: int
    ) -> List[str]:
        """
        收集指定范围内所有帧涉及的价格（含上一帧），排序后返回字符串标签列表。
        用于固定 x 轴 categoryarray，使同价格位置不移动。
        """
        all_prices: set = set()                                    # 价格集合
        # 包含 start_pos-1 以确保差异计算时前一帧的价格也在 x 轴上
        scan_start = max(0, start_pos - 1)                         # 扫描起点
        for pos in range(scan_start, end_pos + 1):                 # 遍历帧范围
            frame = self.loader.get_frame(pos)                     # 取帧
            for side in ["bid", "ask"]:                             # 买卖两侧
                for lv in range(1, MAX_DEPTH + 1):                 # 10 档
                    px = frame.get(f"{side}Px{lv}")                # 价格
                    vlm = frame.get(f"{side}Vlm{lv}", 0)          # 量
                    if pd.notna(px) and vlm > 0:                   # 有效
                        all_prices.add(round(float(px), 2))        # 加入集合
        return [f"{p:.2f}" for p in sorted(all_prices)]            # 排序返回

    # ---- 公开方法 ----

    def plot_single_frame(self, pos: int) -> go.Figure:
        """
        绘制单帧订单簿静态图（堆叠式 base + delta，含分割线）

        参数:
            pos: 帧位置（0-based，在筛选结果中的序号）
        返回:
            go.Figure 对象（可直接 .show()）
        """
        price_labels = self._get_all_price_labels(                 # 获取 x 轴标签
            max(0, pos - 1), pos                                   # 当前帧及前一帧
        )
        traces, title, anns = self._build_stacked_frame_traces(    # 构建堆叠 traces
            pos, price_labels, show_legend=True
        )

        fig = go.Figure(data=traces)                               # 创建图形
        fig.update_layout(                                         # 设置布局
            title=dict(text=title, font=dict(size=14)),            # 标题
            xaxis=dict(                                            # x 轴
                title="价格",                                       # 轴标题
                categoryorder="array",                             # 按数组排序
                categoryarray=price_labels,                        # 固定价格位置
                tickangle=-45,                                     # 标签倾斜
            ),
            yaxis=dict(title="数量"),                               # y 轴标题
            barmode="stack",                                       # 堆叠模式（base + delta）
            template="plotly_white",                               # 白色主题
            height=550,                                            # 图高
            annotations=anns,                                      # 触发类型 + 图例说明
        )
        return fig                                                 # 返回

    def plot_animation(
        self,
        start_pos: int = 0,                                        # 起始帧（默认 0）
        end_pos: Optional[int] = None,                             # 结束帧（默认全部）
        frame_duration: int = 500,                                 # 每帧毫秒数
    ) -> go.Figure:
        """
        创建逐帧动态回放动画（堆叠式 base + delta + 分割线 + 触发类型标签）

        动画策略：
          - x 轴使用全局统一的 categoryarray → 同价格不移动
          - 每帧先在原位展示 delta 变化（堆叠在 base 上方）
          - triggerType 醒目显示在图表右上角

        参数:
            start_pos: 起始位置
            end_pos: 结束位置（含），默认最后一帧
            frame_duration: 每帧持续时间（毫秒）
        返回:
            go.Figure 带动画的图形对象
        """
        if end_pos is None:                                        # 默认取全部
            end_pos = self.loader.n_frames - 1                     # 最后一帧

        # ---- 全局 x 轴标签（保证同价格位置固定不动） ----
        all_labels = self._get_all_price_labels(start_pos, end_pos)

        # ---- 计算 y 轴上限（含 ghost 高度，固定范围避免跳动） ----
        max_vol = 0                                                # 最大量
        for p in range(max(0, start_pos - 1), end_pos + 1):        # 遍历帧（含前一帧）
            frame = self.loader.get_frame(p)                       # 取帧
            for side in ["bid", "ask"]:                             # 两侧
                for lv in range(1, MAX_DEPTH + 1):                 # 10 档
                    v = frame.get(f"{side}Vlm{lv}", 0)            # 取量
                    if v > max_vol:                                 # 更新最大值
                        max_vol = v

        # ---- 构建初始帧 ----
        init_traces, init_title, init_anns = (                     # 初始 traces
            self._build_stacked_frame_traces(
                start_pos, all_labels, show_legend=True
            )
        )

        # ---- 构建所有动画帧 ----
        frames: List[go.Frame] = []                                # 帧列表
        slider_steps: List[Dict] = []                              # 滑块步骤

        for p in range(start_pos, end_pos + 1):                    # 遍历每帧
            traces, title, anns = self._build_stacked_frame_traces(# 构建 traces
                p, all_labels, show_legend=False
            )
            fname = str(p)                                         # 帧名

            frames.append(go.Frame(                                # 动画帧
                data=traces,                                       # 4 个堆叠 traces
                name=fname,                                        # 名称
                layout=dict(                                       # 帧级布局
                    title=dict(text=title),                        # 更新标题
                    annotations=anns,                              # 更新触发标签
                ),
            ))

            adj_idx = int(self.loader.get_frame(p)["adjIndex"])    # 显示索引
            slider_steps.append(dict(                              # 滑块步骤
                args=[[fname], dict(                               # 跳转参数
                    frame=dict(duration=frame_duration, redraw=True),
                    mode="immediate",
                    transition=dict(duration=200),
                )],
                label=str(adj_idx),                                # 标签
                method="animate",                                  # 方法
            ))

        # ---- 组装 Figure ----
        fig = go.Figure(                                           # 创建 Figure
            data=init_traces,                                      # 初始数据（4 traces）
            layout=go.Layout(                                      # 布局
                title=dict(text=init_title, font=dict(size=14)),   # 标题
                xaxis=dict(                                        # x 轴
                    title="价格",
                    categoryorder="array",                         # 按数组排序
                    categoryarray=all_labels,                      # 固定价格位置
                    tickangle=-45,
                ),
                yaxis=dict(                                        # y 轴
                    title="数量",
                    range=[0, max_vol * 1.3],                      # 固定范围（留 ghost 余量）
                ),
                barmode="stack",                                   # 堆叠模式
                template="plotly_white",                           # 白色主题
                height=650,                                        # 图高
                # ---- 播放控制按钮 ----
                updatemenus=[dict(
                    type="buttons",                                # 按钮组
                    showactive=False,
                    y=1.15, x=0.5, xanchor="center",              # 位置
                    buttons=[
                        dict(                                      # ▶ 播放
                            label="▶ 播放",
                            method="animate",
                            args=[None, dict(
                                frame=dict(duration=frame_duration, redraw=True),
                                fromcurrent=True,
                                transition=dict(duration=200),
                            )],
                        ),
                        dict(                                      # ⏸ 暂停
                            label="⏸ 暂停",
                            method="animate",
                            args=[[None], dict(
                                frame=dict(duration=0, redraw=False),
                                mode="immediate",
                                transition=dict(duration=0),
                            )],
                        ),
                    ],
                )],
                # ---- 帧滑块 ----
                sliders=[dict(
                    active=0,                                      # 初始帧
                    steps=slider_steps,                            # 步骤列表
                    x=0.1, len=0.8, xanchor="left",               # 位置
                    y=0, yanchor="top",
                    currentvalue=dict(                             # 当前值标签
                        prefix="帧 adjIndex: ",
                        visible=True,
                        xanchor="center",
                    ),
                    transition=dict(duration=200),
                )],
                # ---- 初始帧的注释 ----
                annotations=init_anns,                             # 触发标签 + 图例
            ),
            frames=frames,                                         # 全部动画帧
        )

        return fig                                                 # 返回动画 Figure
