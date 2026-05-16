# -*- coding: utf-8 -*-
"""visualize_lob.py — 订单簿(Limit Order Book)动态可视化工具.

本模块按依赖顺序包含四个组件:
    1. InternalOrderBook —— 内部撮合引擎, 仅用于生成 toy 数据(不属于对外 API).
    2. generate_toy_data() —— 生成逻辑自洽的 toy 数据(orderbook.parquet / triggerInfo.parquet).
    3. LOBDataLoader —— 读取 parquet 并按股票代码 / 时间 / 索引区间过滤.
    4. LOBVisualizer —— 在 LOBDataLoader 之上做柱状图渲染与逐帧动画.

约定: 蓝色代表买盘, 红色代表卖盘; 深色代表挂单量增加, 浅色代表挂单量减少(撤单或成交).
"""

import os                                  # 用于创建输出目录、拼接文件路径
import numpy as np                         # 用于随机数生成与数值计算
import pandas as pd                        # 用于读写 parquet 与数据过滤
import plotly.graph_objects as go          # 用于绘制交互式柱状图与动画

# 对外暴露的公共符号(InternalOrderBook 是内部实现, 不放入 __all__)
__all__ = ["generate_toy_data", "LOBDataLoader", "LOBVisualizer"]

# ----------------------------------------------------------------------------
# 颜色方案: 蓝=买盘, 红=卖盘; base=当前留存量, inc=新增量(深), dec=减少量(浅)
# ----------------------------------------------------------------------------
BID_BASE = "rgba(33,113,181,0.85)"         # 买盘留存量 —— 中等蓝
BID_INC = "rgba(8,48,107,1.0)"             # 买盘新增量 —— 深蓝(挂单)
BID_DEC = "rgba(158,202,225,0.55)"         # 买盘减少量 —— 浅蓝(撤单/成交的"残影")
ASK_BASE = "rgba(203,24,29,0.85)"          # 卖盘留存量 —— 中等红
ASK_INC = "rgba(103,0,13,1.0)"             # 卖盘新增量 —— 深红(挂单)
ASK_DEC = "rgba(252,174,145,0.55)"         # 卖盘减少量 —— 浅红(撤单/成交的"残影")
DIV_LINE = "rgba(0,0,0,0.9)"               # 分割线颜色 —— 黑色, 用于区分"原本"与"变化"


# ----------------------------------------------------------------------------
# 时间编码: time / serverTime 用整数表示, 格式为 HHMMSSmmm
# 即 时*10^7 + 分*10^5 + 秒*10^3 + 毫秒, 例如 09:39:49.000 -> 93949000
# 该编码在同一交易日内与真实时间严格保持单调一致(各字段位宽足够、不进位).
# ----------------------------------------------------------------------------
def _ms_to_hhmmssmmm(ms):
    """把"自午夜起的毫秒数"编码为 HHMMSSmmm 整数.

    参数:
        ms: 自午夜 00:00:00.000 起的毫秒数(整数).
    返回:
        HHMMSSmmm 格式的整数, 如 09:39:49.000 -> 93949000.
    """
    ms = int(ms)                           # 确保是整数
    h = ms // 3_600_000                    # 小时 = 总毫秒 // 每小时毫秒
    m = (ms // 60_000) % 60                # 分钟 = (总毫秒 // 每分钟毫秒) 对 60 取模
    s = (ms // 1_000) % 60                 # 秒 = (总毫秒 // 每秒毫秒) 对 60 取模
    ms3 = ms % 1_000                       # 毫秒 = 总毫秒对 1000 取模
    return h * 10_000_000 + m * 100_000 + s * 1_000 + ms3   # 按位拼成 HHMMSSmmm


def _format_time(t):
    """把 HHMMSSmmm 整数解码为可读字符串 HH:MM:SS.mmm(仅用于图上展示).

    参数:
        t: HHMMSSmmm 格式的整数.
    返回:
        形如 "09:39:49.000" 的字符串.
    """
    t = int(t)                             # 确保是整数
    h = t // 10_000_000                    # 取高位的小时
    m = (t // 100_000) % 100               # 取分钟段
    s = (t // 1_000) % 100                 # 取秒段
    ms3 = t % 1_000                        # 取毫秒段
    return f"{h:02d}:{m:02d}:{s:02d}.{ms3:03d}"   # 补零格式化


# ============================================================================
# 1. InternalOrderBook —— 内部撮合引擎(仅供 toy 数据生成使用)
# ============================================================================
class InternalOrderBook:
    """一个最小可用的限价订单簿撮合引擎.

    维护买卖两侧的价格档位, 每个档位是一个按时间先后排列的订单队列,
    撮合遵循通用的"价格优先、时间优先"规则. 只支持两种操作:
        - place_limit_order: 挂限价单(可能产生零成交/部分成交/全部成交)
        - cancel_order: 撤单(可撤未成交或部分成交的单子)
    """

    def __init__(self, tick=0.01):
        """初始化空订单簿.

        参数:
            tick: 最小价格变动单位(A 股通常为 0.01 元).
        """
        self.tick = tick                   # 最小价格变动单位
        self.bids = {}                     # 买盘: price -> [[order_id, volume], ...](按时间先后)
        self.asks = {}                     # 卖盘: price -> [[order_id, volume], ...](按时间先后)
        self.orders = {}                   # order_id -> (side, price), 便于撤单时快速定位
        self._next_id = 0                  # 自增订单号计数器

    def _round(self, price):
        """把价格对齐到 tick 网格上, 避免浮点误差导致的"伪档位"."""
        return round(round(price / self.tick) * self.tick, 4)

    def _new_id(self):
        """生成一个全局唯一的订单号."""
        oid = self._next_id                # 取当前计数值作为新订单号
        self._next_id += 1                 # 计数器自增
        return oid

    def best_bid(self):
        """返回最优买价(买一); 若买盘为空返回 None."""
        return max(self.bids) if self.bids else None

    def best_ask(self):
        """返回最优卖价(卖一); 若卖盘为空返回 None."""
        return min(self.asks) if self.asks else None

    def place_limit_order(self, side, price, volume):
        """挂一笔限价单, 先与对手盘撮合, 剩余量挂入本方订单簿.

        参数:
            side: 'buy' 或 'sell'.
            price: 限价单价格.
            volume: 下单数量(整数).
        """
        price = self._round(price)         # 价格对齐到 tick 网格
        volume = int(volume)               # 数量取整
        if side == "buy":                  # ---------- 买单 ----------
            # 买单与卖盘撮合: 价格优先(最低卖价先成交), 时间优先(最早的单先成交)
            while volume > 0 and self.asks:                 # 仍有剩余量且卖盘非空
                best = min(self.asks)                       # 当前最优(最低)卖价
                if best > price:                            # 最优卖价高于买单限价 -> 无法成交
                    break
                queue = self.asks[best]                     # 取该价位的订单队列
                while volume > 0 and queue:                 # 逐笔吃掉队首订单(时间优先)
                    head = queue[0]                         # 队首订单 [order_id, volume]
                    traded = min(volume, head[1])           # 本次成交量 = 两者较小值
                    head[1] -= traded                       # 减少对手订单的剩余量
                    volume -= traded                        # 减少本方订单的剩余量
                    if head[1] == 0:                        # 对手订单被完全吃掉
                        del self.orders[head[0]]            # 从订单索引中移除
                        queue.pop(0)                        # 从队列中移除
                if not queue:                               # 该价位已无挂单
                    del self.asks[best]                     # 删除该价位档位
            if volume > 0:                                  # 仍有剩余量 -> 挂为新的买单
                oid = self._new_id()                        # 申请新订单号
                self.bids.setdefault(price, []).append([oid, volume])  # 入队(排在最后, 时间最晚)
                self.orders[oid] = ("buy", price)           # 记录订单位置
        else:                              # ---------- 卖单 ----------
            # 卖单与买盘撮合: 价格优先(最高买价先成交), 时间优先(最早的单先成交)
            while volume > 0 and self.bids:                 # 仍有剩余量且买盘非空
                best = max(self.bids)                       # 当前最优(最高)买价
                if best < price:                            # 最优买价低于卖单限价 -> 无法成交
                    break
                queue = self.bids[best]                     # 取该价位的订单队列
                while volume > 0 and queue:                 # 逐笔吃掉队首订单(时间优先)
                    head = queue[0]                         # 队首订单 [order_id, volume]
                    traded = min(volume, head[1])           # 本次成交量
                    head[1] -= traded                       # 减少对手订单的剩余量
                    volume -= traded                        # 减少本方订单的剩余量
                    if head[1] == 0:                        # 对手订单被完全吃掉
                        del self.orders[head[0]]            # 从订单索引中移除
                        queue.pop(0)                        # 从队列中移除
                if not queue:                               # 该价位已无挂单
                    del self.bids[best]                     # 删除该价位档位
            if volume > 0:                                  # 仍有剩余量 -> 挂为新的卖单
                oid = self._new_id()                        # 申请新订单号
                self.asks.setdefault(price, []).append([oid, volume])  # 入队(时间最晚)
                self.orders[oid] = ("sell", price)          # 记录订单位置

    def cancel_order(self, order_id):
        """撤掉指定订单号的挂单(可能是未成交或部分成交的单子).

        返回:
            True 表示撤单成功, False 表示订单不存在.
        """
        if order_id not in self.orders:    # 订单号不存在(可能已全部成交)
            return False
        side, price = self.orders[order_id]            # 取出订单所在的方向与价位
        book = self.bids if side == "buy" else self.asks  # 选择对应的订单簿一侧
        queue = book.get(price, [])                     # 取该价位的订单队列
        for i, o in enumerate(queue):                   # 在队列中查找该订单
            if o[0] == order_id:                        # 找到了
                queue.pop(i)                            # 从队列中移除
                break
        if not queue:                                   # 该价位已无挂单
            book.pop(price, None)                       # 删除该价位档位
        del self.orders[order_id]                       # 从订单索引中移除
        return True

    def pick_random_order(self, side, rng, top_levels=12):
        """在某一侧的前若干档中随机挑选一个可撤订单号(供 toy 数据生成调用).

        只在靠近盘口的档位里挑选, 以提高"撤单会改变前 10 档"的概率.

        参数:
            side: 'buy' 或 'sell'.
            rng: numpy 随机数生成器.
            top_levels: 仅考虑最靠近盘口的多少个价位.
        返回:
            一个订单号; 若该侧没有挂单则返回 None.
        """
        book = self.bids if side == "buy" else self.asks   # 选择订单簿一侧
        if not book:                                       # 该侧没有任何挂单
            return None
        # 买盘价高者更靠近盘口(降序), 卖盘价低者更靠近盘口(升序)
        prices = sorted(book, reverse=(side == "buy"))[:top_levels]
        candidates = [o[0] for p in prices for o in book[p]]  # 收集这些档位上的所有订单号
        if not candidates:                                 # 没有候选订单
            return None
        return int(rng.choice(candidates))                 # 随机返回一个订单号

    def snapshot(self, depth=10):
        """生成当前订单簿的前 depth 档快照.

        返回:
            (bids, asks), 其中 bids 是 [(price, volume), ...] 按价格降序,
            asks 是 [(price, volume), ...] 按价格升序; volume 为该价位的总挂单量.
        """
        bid_prices = sorted(self.bids, reverse=True)[:depth]  # 买盘取价高的前 depth 档
        ask_prices = sorted(self.asks)[:depth]                # 卖盘取价低的前 depth 档
        bids = [(p, sum(o[1] for o in self.bids[p])) for p in bid_prices]  # 汇总每档总量
        asks = [(p, sum(o[1] for o in self.asks[p])) for p in ask_prices]  # 汇总每档总量
        return bids, asks


# ============================================================================
# 2. generate_toy_data() —— 生成逻辑自洽的 toy 数据
# ============================================================================
def _make_ob_row(code, adj_index, time, server_time, bids, asks, depth=10):
    """把一份订单簿快照打包成 orderbook.parquet 的一行(字典).

    参数:
        code: 股票代码(int).
        adj_index: 该帧的索引(int).
        time: 交易所时间(int, HHMMSSmmm 格式).
        server_time: 本地收到行情的时间(int, HHMMSSmmm 格式).
        bids/asks: snapshot() 返回的档位列表.
        depth: 档位数(默认 10).
    """
    row = {"code": code, "adjIndex": adj_index, "time": time, "serverTime": server_time}
    for i in range(depth):                          # 填充买 1~depth 档
        if i < len(bids):                           # 该档存在
            row[f"bidPx{i + 1}"] = bids[i][0]       # 买 i+1 档价格
            row[f"bidVlm{i + 1}"] = bids[i][1]      # 买 i+1 档挂单量
        else:                                       # 该档不存在(订单簿薄于 depth 档)
            row[f"bidPx{i + 1}"] = np.nan           # 价格记为缺失
            row[f"bidVlm{i + 1}"] = 0               # 挂单量记为 0
    for i in range(depth):                          # 填充卖 1~depth 档
        if i < len(asks):                           # 该档存在
            row[f"askPx{i + 1}"] = asks[i][0]       # 卖 i+1 档价格
            row[f"askVlm{i + 1}"] = asks[i][1]      # 卖 i+1 档挂单量
        else:                                       # 该档不存在
            row[f"askPx{i + 1}"] = np.nan           # 价格记为缺失
            row[f"askVlm{i + 1}"] = 0               # 挂单量记为 0
    return row


def generate_toy_data(out_dir="toy_data", n_events=100, code=600519, seed=42):
    """生成一份逻辑自洽的 toy 订单簿数据并写入 parquet 文件.

    "逻辑自洽"指数据严格符合订单簿的工作原理: 所有变化都由一笔真实的
    限价单或撤单产生, 因此不会出现"跳过买一与买二成交""相邻两帧买一买二
    同时增加挂单量"等不可能的情况.

    参数:
        out_dir: 输出目录(自动创建).
        n_events: 触发事件数量, 即 triggerInfo 的行数; orderbook 行数为 n_events + 1.
        code: 股票代码.
        seed: 随机数种子, 保证结果可复现.
    返回:
        (orderbook_df, trigger_df) 两个 DataFrame.
    """
    os.makedirs(out_dir, exist_ok=True)             # 确保输出目录存在
    rng = np.random.default_rng(seed)               # 创建可复现的随机数生成器
    tick = 0.01                                     # 价格最小变动单位
    ob = InternalOrderBook(tick=tick)               # 创建内部撮合引擎
    mid = 100.00                                    # 初始中间价

    # ---- 初始化: 在中间价两侧各挂 30 档限价单, 形成一个较深的初始订单簿 ----
    for i in range(1, 31):                          # i 表示距中间价的档位数
        ob.place_limit_order("buy", mid - i * tick, int(rng.integers(5, 50)))   # 买盘挂在中间价下方
        ob.place_limit_order("sell", mid + i * tick, int(rng.integers(5, 50)))  # 卖盘挂在中间价上方

    ob_rows = []                                    # 收集 orderbook.parquet 的行
    tr_rows = []                                    # 收集 triggerInfo.parquet 的行
    ts_ms = 9 * 3_600_000 + 30 * 60_000             # 起始交易所时间: 09:30:00.000 的"自午夜毫秒数"
    adj = int(rng.integers(1, 5))                   # 起始 adjIndex(随机起点)

    # ---- 记录初始帧(第 0 帧, 无触发事件) ----
    bids, asks = ob.snapshot(10)                    # 取初始快照
    server_ms = ts_ms + int(rng.integers(1, 8))     # 本地时间略晚于交易所时间(1~7 毫秒)
    ob_rows.append(_make_ob_row(code, adj, _ms_to_hhmmssmmm(ts_ms),
                                _ms_to_hhmmssmmm(server_ms), bids, asks))  # 写入第 0 帧
    last_bids, last_asks = bids, asks               # 记下"上一帧已记录的快照"用于去重

    # ---- 事件循环: 不断产生事件, 直到记录满 n_events 个触发 ----
    guard = 0                                       # 死循环保护计数器
    while len(tr_rows) < n_events and guard < 1_000_000:
        guard += 1                                  # 每轮迭代自增
        bb = ob.best_bid()                          # 当前买一价
        ba = ob.best_ask()                          # 当前卖一价
        n_bid_levels = len(ob.bids)                 # 当前买盘档位数
        n_ask_levels = len(ob.asks)                 # 当前卖盘档位数

        # 选择事件类型: 若某侧档位过薄, 强制补充该侧流动性, 否则按概率随机
        if n_bid_levels < 12:                       # 买盘太薄
            action = "passive_buy"                  # 强制挂被动买单补充
        elif n_ask_levels < 12:                     # 卖盘太薄
            action = "passive_sell"                 # 强制挂被动卖单补充
        else:                                       # 盘口健康, 按概率随机选择
            action = rng.choice(
                ["passive_buy", "passive_sell", "agg_buy", "agg_sell", "cancel_buy", "cancel_sell"],
                p=[0.24, 0.24, 0.11, 0.11, 0.15, 0.15],   # 被动挂单概率最高, 主动单与撤单次之
            )

        trigger_type = None                         # 本次事件对应的 triggerType

        if action == "passive_buy":                 # ---- 被动买单(挂在盘口或更深处, 不跨过卖一) ----
            offset = int(rng.integers(0, 8))        # 距买一的档位偏移
            price = round(bb - offset * tick, 2)    # 默认挂在买一或更下方
            if offset == 0 and rng.random() < 0.4 and (ba - bb) > tick + 1e-9:
                price = round(bb + tick, 2)         # 有时改善买一(挂进价差内), 但不跨过卖一
            ob.place_limit_order("buy", price, int(rng.integers(3, 40)))  # 执行挂单
            trigger_type = "order"                  # 限价挂单 -> triggerType 为 order
        elif action == "passive_sell":              # ---- 被动卖单(挂在盘口或更深处, 不跨过买一) ----
            offset = int(rng.integers(0, 8))        # 距卖一的档位偏移
            price = round(ba + offset * tick, 2)    # 默认挂在卖一或更上方
            if offset == 0 and rng.random() < 0.4 and (ba - bb) > tick + 1e-9:
                price = round(ba - tick, 2)         # 有时改善卖一(挂进价差内), 但不跨过买一
            ob.place_limit_order("sell", price, int(rng.integers(3, 40)))  # 执行挂单
            trigger_type = "order"                  # 限价挂单 -> triggerType 为 order
        elif action == "agg_buy":                   # ---- 主动买单(跨过价差吃掉若干卖档) ----
            price = round(ba + int(rng.integers(0, 3)) * tick, 2)  # 限价跨过卖一 0~2 档
            ob.place_limit_order("buy", price, int(rng.integers(3, 25)))  # 体量适中, 避免打穿订单簿
            trigger_type = "order"                  # 成交本质上也由 order 引起 -> triggerType 为 order
        elif action == "agg_sell":                  # ---- 主动卖单(跨过价差吃掉若干买档) ----
            price = round(bb - int(rng.integers(0, 3)) * tick, 2)  # 限价跨过买一 0~2 档
            ob.place_limit_order("sell", price, int(rng.integers(3, 25)))  # 体量适中
            trigger_type = "order"                  # triggerType 为 order
        elif action == "cancel_buy":                # ---- 撤买单 ----
            oid = ob.pick_random_order("buy", rng)  # 在买盘前若干档随机挑一个订单
            if oid is None:                         # 没有可撤订单
                continue                            # 跳过本轮
            ob.cancel_order(oid)                    # 执行撤单
            trigger_type = "cancel"                 # 撤单 -> triggerType 为 cancel
        else:                                       # ---- 撤卖单 ----
            oid = ob.pick_random_order("sell", rng)  # 在卖盘前若干档随机挑一个订单
            if oid is None:                         # 没有可撤订单
                continue                            # 跳过本轮
            ob.cancel_order(oid)                    # 执行撤单
            trigger_type = "cancel"                 # 撤单 -> triggerType 为 cancel

        bids, asks = ob.snapshot(10)                # 取事件后的最新快照
        if (bids, asks) == (last_bids, last_asks):  # 前 10 档没有发生变化
            continue                                # 不记录(只记录前 10 档真正变化的帧)

        # 前 10 档确实变化了 -> 推进时间与索引, 并记录新的一行
        ts_ms += int(rng.integers(50, 800))         # 交易所时间前进 50~799 毫秒
        adj += int(rng.integers(1, 5))              # adjIndex 单调递增, 步长 1~4(允许跳号)
        server_ms = ts_ms + int(rng.integers(1, 8))  # 本地时间略晚于交易所时间
        ob_rows.append(_make_ob_row(code, adj, _ms_to_hhmmssmmm(ts_ms),
                                    _ms_to_hhmmssmmm(server_ms), bids, asks))  # 写入 orderbook 行
        tr_rows.append({"code": code, "adjIndex": adj, "triggerType": trigger_type})  # 写入 trigger 行
        last_bids, last_asks = bids, asks           # 更新"上一帧已记录的快照"

    # ---- 整理为 DataFrame 并强制关键列的数据类型 ----
    orderbook_df = pd.DataFrame(ob_rows)            # orderbook 表
    trigger_df = pd.DataFrame(tr_rows)              # triggerInfo 表
    orderbook_df["code"] = orderbook_df["code"].astype("int64")          # code 必须是整数
    orderbook_df["adjIndex"] = orderbook_df["adjIndex"].astype("int64")  # adjIndex 必须是整数
    orderbook_df["time"] = orderbook_df["time"].astype("int64")          # time 为 HHMMSSmmm 整数
    orderbook_df["serverTime"] = orderbook_df["serverTime"].astype("int64")  # serverTime 同上
    trigger_df["code"] = trigger_df["code"].astype("int64")              # code 必须是整数
    trigger_df["adjIndex"] = trigger_df["adjIndex"].astype("int64")      # adjIndex 必须是整数

    # ---- 写入 parquet 文件 ----
    ob_path = os.path.join(out_dir, "orderbook.parquet")        # orderbook 文件路径
    tr_path = os.path.join(out_dir, "triggerInfo.parquet")      # triggerInfo 文件路径
    orderbook_df.to_parquet(ob_path, index=False)               # 写出 orderbook
    trigger_df.to_parquet(tr_path, index=False)                 # 写出 triggerInfo
    print(f"已生成 toy 数据: {ob_path} ({len(orderbook_df)} 帧), {tr_path} ({len(trigger_df)} 个事件)")
    return orderbook_df, trigger_df


# ============================================================================
# 3. LOBDataLoader —— 数据读取与过滤
# ============================================================================
class LOBDataLoader:
    """读取 orderbook / triggerInfo 两个 parquet 文件, 并提供过滤与逐帧访问接口."""

    def __init__(self, orderbook_path, triggerinfo_path):
        """读入两个 parquet 文件.

        参数:
            orderbook_path: orderbook.parquet 的路径.
            triggerinfo_path: triggerInfo.parquet 的路径.
        """
        self.orderbook_path = orderbook_path        # 记录 orderbook 文件路径
        self.triggerinfo_path = triggerinfo_path    # 记录 triggerInfo 文件路径
        self._ob_all = pd.read_parquet(orderbook_path)       # 读入全部 orderbook 数据
        self._tr_all = pd.read_parquet(triggerinfo_path)     # 读入全部 triggerInfo 数据
        # 默认工作视图 = 全部数据, 按 (code, adjIndex) 排序保证逐帧顺序正确
        self.frames = self._ob_all.sort_values(["code", "adjIndex"]).reset_index(drop=True)

    def filter(self, code=None, start_time=None, end_time=None,
               start_index=None, end_index=None):
        """按股票代码 / 时间区间 / adjIndex 区间过滤, 结果保存为当前工作视图.

        参数:
            code: 股票代码; None 表示不限.
            start_time / end_time: 交易所时间(time 列)区间, 闭区间; None 表示不限.
                取值为 HHMMSSmmm 格式整数(如 93000000 表示 09:30:00.000).
            start_index / end_index: adjIndex 区间, 闭区间; None 表示不限.
        返回:
            self, 以支持链式调用.
        """
        df = self._ob_all                          # 从全量数据开始过滤
        if code is not None:                        # 指定了股票代码
            df = df[df["code"] == code]
        if start_time is not None:                  # 指定了起始时间(HHMMSSmmm 整数)
            df = df[df["time"] >= start_time]       # 该编码与真实时间同序, 可直接整数比较
        if end_time is not None:                    # 指定了结束时间(HHMMSSmmm 整数)
            df = df[df["time"] <= end_time]
        if start_index is not None:                 # 指定了起始 adjIndex
            df = df[df["adjIndex"] >= start_index]
        if end_index is not None:                   # 指定了结束 adjIndex
            df = df[df["adjIndex"] <= end_index]
        # 过滤结果按 (code, adjIndex) 排序后作为新的工作视图
        self.frames = df.sort_values(["code", "adjIndex"]).reset_index(drop=True)
        return self

    def __len__(self):
        """工作视图中的帧数量."""
        return len(self.frames)

    def get_frame(self, pos):
        """按位置(0 起的下标)取出工作视图中的一帧, 返回一个 pandas Series."""
        if pos < 0 or pos >= len(self.frames):      # 位置越界检查
            raise IndexError(f"帧位置 {pos} 越界, 有效范围 0~{len(self.frames) - 1}")
        return self.frames.iloc[pos]

    def get_trigger(self, pos):
        """取出工作视图中第 pos 帧对应的触发信息.

        通过 (code, adjIndex) 双元组把 orderbook 行 merge 到 triggerInfo 上.
        返回一个 pandas Series; 若该帧没有触发信息(例如初始帧)则返回 None.
        """
        row = self.get_frame(pos)                   # 取出该帧
        match = self._tr_all[                       # 在 triggerInfo 中查找同 (code, adjIndex) 的行
            (self._tr_all["code"] == row["code"])
            & (self._tr_all["adjIndex"] == row["adjIndex"])
        ]
        if len(match) == 0:                         # 没有匹配(初始帧无触发)
            return None
        return match.iloc[0]                        # 返回第一条匹配


# ============================================================================
# 4. LOBVisualizer —— 柱状图渲染与逐帧动画
# ============================================================================
class LOBVisualizer:
    """在 LOBDataLoader 之上提供静态单帧绘图与交互式逐帧动画."""

    def __init__(self, loader, tick=None):
        """初始化可视化器.

        参数:
            loader: 一个 LOBDataLoader 实例.
            tick: 价格最小变动单位; 为 None 时自动从数据中推断.
        """
        self.loader = loader                        # 持有数据加载器
        self.tick = tick if tick is not None else self._infer_tick(loader._ob_all)  # 价格 tick
        self.bar_width = self.tick * 0.8            # 柱子宽度略小于 tick, 避免相邻柱子相接

    # ------------------------------------------------------------------ #
    # 内部辅助方法
    # ------------------------------------------------------------------ #
    @staticmethod
    def _infer_tick(df):
        """从全量数据的所有价格列中推断最小价格变动单位."""
        px_cols = [c for c in df.columns if c.startswith("bidPx") or c.startswith("askPx")]
        vals = pd.unique(df[px_cols].values.ravel())            # 收集所有出现过的价格
        vals = np.sort(np.array([v for v in vals if pd.notna(v)], dtype=float))  # 去掉缺失并排序
        diffs = np.diff(vals)                                   # 相邻价格之差
        diffs = diffs[diffs > 1e-9]                             # 仅保留正的差值
        return float(round(diffs.min(), 4)) if len(diffs) else 0.01  # 最小正差值即为 tick

    @staticmethod
    def _row_to_maps(row):
        """把 orderbook 的一行拆成 {价格: 挂单量} 的买盘字典与卖盘字典.

        以"价格"为键(而非档位序号), 这样后续比较两帧时, 价格平移不会被误判为
        多个档位同时变化 —— 这正是 PLAN 中强调的逻辑要点.
        """
        bid_map = {}                                # 买盘 价格->量
        ask_map = {}                                # 卖盘 价格->量
        for i in range(1, 11):                      # 遍历 1~10 档
            bp, bv = row.get(f"bidPx{i}"), row.get(f"bidVlm{i}")   # 买 i 档价与量
            if pd.notna(bp) and pd.notna(bv) and bv > 0:           # 该档有效
                key = round(float(bp), 4)                          # 价格对齐
                bid_map[key] = bid_map.get(key, 0.0) + float(bv)   # 累加(理论上同价只会出现一次)
            ap, av = row.get(f"askPx{i}"), row.get(f"askVlm{i}")   # 卖 i 档价与量
            if pd.notna(ap) and pd.notna(av) and av > 0:           # 该档有效
                key = round(float(ap), 4)                          # 价格对齐
                ask_map[key] = ask_map.get(key, 0.0) + float(av)   # 累加
        return bid_map, ask_map

    @staticmethod
    def _decompose(cur_map, prev_map):
        """比较当前帧与上一帧, 把每个价格的挂单量分解为 留存 / 新增 / 减少 三部分.

        参数:
            cur_map / prev_map: {价格: 量} 字典.
        返回:
            (prices, base, inc, dec) 四个等长列表, 按价格升序:
                base —— 两帧共有的留存量 = min(当前, 上一帧)
                inc  —— 新增量 = max(0, 当前 - 上一帧)(挂单或主动单剩余, 用深色)
                dec  —— 减少量 = max(0, 上一帧 - 当前)(撤单或成交, 用浅色"残影")
        """
        prices = sorted(set(cur_map) | set(prev_map))   # 两帧出现过的所有价格(升序)
        base, inc, dec = [], [], []                     # 三个分量列表
        for p in prices:                                # 逐个价格计算
            c = float(cur_map.get(p, 0.0))              # 当前帧在该价格的量
            v = float(prev_map.get(p, 0.0))             # 上一帧在该价格的量
            base.append(min(c, v))                      # 留存量
            inc.append(max(0.0, c - v))                 # 新增量
            dec.append(max(0.0, v - c))                 # 减少量
        return prices, base, inc, dec

    def _build_bars(self, dec_bid, dec_ask):
        """根据买/卖两侧的分解结果, 构造 6 条柱状图 trace.

        6 条 trace 的顺序在所有动画帧中保持一致(plotly 动画按 trace 下标匹配):
            0 买盘留存  1 买盘新增  2 买盘减少  3 卖盘留存  4 卖盘新增  5 卖盘减少
        """
        pb, bb, ib, db = dec_bid                        # 买盘: 价格/留存/新增/减少
        pa, ba, ia, da = dec_ask                        # 卖盘: 价格/留存/新增/减少
        w = self.bar_width                              # 柱子宽度
        cur_b = [x + y for x, y in zip(bb, ib)]         # 买盘各价格的当前总量(留存+新增)
        cur_a = [x + y for x, y in zip(ba, ia)]         # 卖盘各价格的当前总量
        # 减少量"残影"堆叠的起点 = 留存 + 新增(即当前真实柱高之上)
        dec_base_b = [x + y for x, y in zip(bb, ib)]    # 买盘残影起点
        dec_base_a = [x + y for x, y in zip(ba, ia)]    # 卖盘残影起点
        return [
            # trace 0: 买盘留存量(中蓝, 从 0 开始)
            go.Bar(x=pb, y=bb, base=[0.0] * len(pb), width=w, name="买盘",
                   marker_color=BID_BASE, customdata=cur_b,
                   hovertemplate="买 价格 %{x:.2f}<br>当前挂单量 %{customdata:.0f}<extra></extra>"),
            # trace 1: 买盘新增量(深蓝, 堆在留存量之上)
            go.Bar(x=pb, y=ib, base=bb, width=w, name="买盘·挂单增加",
                   marker_color=BID_INC,
                   hovertemplate="买 价格 %{x:.2f}<br>本帧新增 %{y:.0f}<extra></extra>"),
            # trace 2: 买盘减少量(浅蓝残影, 堆在当前柱高之上)
            go.Bar(x=pb, y=db, base=dec_base_b, width=w, name="买盘·撤单/成交",
                   marker_color=BID_DEC,
                   hovertemplate="买 价格 %{x:.2f}<br>本帧减少 %{y:.0f}<extra></extra>"),
            # trace 3: 卖盘留存量(中红, 从 0 开始)
            go.Bar(x=pa, y=ba, base=[0.0] * len(pa), width=w, name="卖盘",
                   marker_color=ASK_BASE, customdata=cur_a,
                   hovertemplate="卖 价格 %{x:.2f}<br>当前挂单量 %{customdata:.0f}<extra></extra>"),
            # trace 4: 卖盘新增量(深红, 堆在留存量之上)
            go.Bar(x=pa, y=ia, base=ba, width=w, name="卖盘·挂单增加",
                   marker_color=ASK_INC,
                   hovertemplate="卖 价格 %{x:.2f}<br>本帧新增 %{y:.0f}<extra></extra>"),
            # trace 5: 卖盘减少量(浅红残影, 堆在当前柱高之上)
            go.Bar(x=pa, y=da, base=dec_base_a, width=w, name="卖盘·撤单/成交",
                   marker_color=ASK_DEC,
                   hovertemplate="卖 价格 %{x:.2f}<br>本帧减少 %{y:.0f}<extra></extra>"),
        ]

    def _divider_shapes(self, dec):
        """为发生变化的档位生成"分割线"形状, 把变化部分与原本部分区分开.

        分割线画在留存量的顶部(y = base), 横跨整根柱子的宽度.
        参数:
            dec: _decompose() 的返回值.
        """
        prices, base, inc, dec_ = dec               # 价格/留存/新增/减少
        w = self.bar_width                          # 柱子宽度
        shapes = []                                 # 收集线形状
        for p, b, i_, d_ in zip(prices, base, inc, dec_):
            # 仅当该档既有留存量又有变化量时才画分割线(纯新增/纯消失的柱子无需分割)
            if b > 1e-9 and (i_ > 1e-9 or d_ > 1e-9):
                shapes.append(dict(
                    type="line", xref="x", yref="y",
                    x0=p - w / 2, x1=p + w / 2,     # 线段横跨整根柱子
                    y0=b, y1=b,                     # 画在留存量顶部
                    line=dict(color=DIV_LINE, width=2.5),
                    layer="above",                  # 压在柱子之上
                ))
        return shapes

    @staticmethod
    def _annotation(text):
        """构造一个显示在图顶部的文字标注(用于显示帧信息与 triggerType)."""
        return dict(
            text=text, xref="paper", yref="paper", x=0.5, y=1.07,
            showarrow=False, font=dict(size=14, color="#222"),
            bgcolor="rgba(255,235,160,0.85)", bordercolor="#999", borderwidth=1,
        )

    def _xrange(self, *maps):
        """根据若干 {价格:量} 字典计算合适的 x 轴范围(左右各留 5 个 tick 的边距)."""
        prices = [p for m in maps for p in m]       # 汇总所有价格
        if not prices:                              # 没有价格(空盘)
            return [99.0, 101.0]                    # 返回一个兜底范围
        return [min(prices) - 5 * self.tick, max(prices) + 5 * self.tick]

    # ------------------------------------------------------------------ #
    # 公开方法: 静态单帧绘图
    # ------------------------------------------------------------------ #
    def plot_single_frame(self, pos):
        """绘制工作视图中第 pos 帧的订单簿, 并叠加与上一帧的差异.

        柱状图: x 轴为价格, y 轴为挂单量; 蓝=买盘, 红=卖盘;
        每根柱子从下到上依次为 留存量(中色) / 新增量(深色) / 减少量残影(浅色),
        留存量与变化量之间用黑色分割线区分.

        参数:
            pos: 帧位置(0 起的下标).
        返回:
            一个 plotly Figure.
        """
        n = len(self.loader)                        # 工作视图帧数
        if pos < 0 or pos >= n:                     # 位置越界检查
            raise IndexError(f"帧位置 {pos} 越界, 有效范围 0~{n - 1}")
        row = self.loader.get_frame(pos)            # 取当前帧
        cur_b, cur_a = self._row_to_maps(row)       # 当前帧的买/卖盘价量字典
        if pos == 0:                                # 第 0 帧没有上一帧
            prev_b, prev_a = cur_b, cur_a           # 把上一帧视作与当前帧相同 -> 无变化
        else:                                       # 非首帧
            prev_b, prev_a = self._row_to_maps(self.loader.get_frame(pos - 1))  # 上一帧价量字典

        dec_bid = self._decompose(cur_b, prev_b)    # 买盘分解(留存/新增/减少)
        dec_ask = self._decompose(cur_a, prev_a)    # 卖盘分解
        bars = self._build_bars(dec_bid, dec_ask)   # 构造 6 条柱状图 trace
        shapes = self._divider_shapes(dec_bid) + self._divider_shapes(dec_ask)  # 分割线

        # 组织标题信息: 股票代码 / adjIndex / 时间 / 触发类型
        trig = self.loader.get_trigger(pos)         # 该帧的触发信息
        ttype = trig["triggerType"] if trig is not None else "无(初始帧)"  # 触发类型文字
        title = (f"订单簿快照　第 {pos} 帧　|　code={int(row['code'])}　"
                 f"adjIndex={int(row['adjIndex'])}　|　时间 {_format_time(row['time'])}　|　"
                 f"触发类型: {ttype}")

        fig = go.Figure(data=bars)                  # 用 6 条 trace 创建图
        fig.update_layout(
            barmode="overlay",                      # 用 overlay 模式(已通过 base 参数手动堆叠)
            title=dict(text=title, x=0.5, font=dict(size=14)),
            shapes=shapes,                          # 叠加分割线
            xaxis=dict(title="价格", range=self._xrange(cur_b, cur_a, prev_b, prev_a)),
            yaxis=dict(title="挂单量(手)", rangemode="tozero"),
            legend=dict(orientation="h", y=-0.18),  # 图例横向放在底部
            plot_bgcolor="white", width=960, height=560,
        )
        return fig

    # ------------------------------------------------------------------ #
    # 公开方法: 交互式逐帧动画
    # ------------------------------------------------------------------ #
    def plot_animation(self, start=0, end=None, frame_duration=600, transition_duration=350):
        """绘制工作视图中 [start, end] 区间的逐帧动画.

        动画采用 PLAN 要求的"两阶段"方式: 对相邻两帧,
            - 变化阶段(change): x 轴范围保持上一帧不动, 先展示挂单量的增减变化;
            - 平移阶段(shift): 数据不变, x 轴范围平移到当前帧的盘口位置.
        每帧顶部以文字标注显示该帧的 triggerType.

        参数:
            start / end: 帧区间(0 起下标); end 为 None 时取到最后一帧.
            frame_duration: 播放时每帧停留的毫秒数.
            transition_duration: 帧间过渡动画的毫秒数.
        返回:
            一个带播放/暂停按钮与滑块的 plotly Figure.
        """
        n = len(self.loader)                        # 工作视图帧数
        if n == 0:                                  # 没有数据
            raise ValueError("工作视图中没有任何帧")
        if end is None:                             # 未指定结束位置
            end = n - 1                             # 默认到最后一帧
        if not (0 <= start <= end < n):             # 区间合法性检查
            raise ValueError(f"非法的帧区间 [{start}, {end}], 有效范围 0~{n - 1}")
        idxs = list(range(start, end + 1))          # 区间内所有帧的位置下标

        # ---- 预处理: 计算每帧的价量字典 ----
        maps = [self._row_to_maps(self.loader.get_frame(p)) for p in idxs]

        # ---- 计算全局 y 轴上限, 使柱高在所有帧之间可比且不跳动 ----
        all_vol = [v for (bm, am) in maps for v in list(bm.values()) + list(am.values())]
        y_max = (max(all_vol) * 1.2) if all_vol else 1.0

        # ---- 逐帧计算分解结果与 x 轴范围 ----
        decomps = []                                # 每项: dict(bid=..., ask=..., xrange=...)
        for k in range(len(idxs)):                  # k 是区间内的序号
            cur_b, cur_a = maps[k]                  # 当前帧价量字典
            if k == 0:                              # 区间首帧没有"上一帧"
                prev_b, prev_a = cur_b, cur_a       # 视作无变化(全部为留存量)
            else:                                   # 非首帧
                prev_b, prev_a = maps[k - 1]        # 取区间内的上一帧
            decomps.append(dict(
                bid=self._decompose(cur_b, prev_b),         # 买盘分解
                ask=self._decompose(cur_a, prev_a),         # 卖盘分解
                xrange=self._xrange(cur_b, cur_a),          # 当前帧的 x 轴范围
            ))

        # ---- 构造动画帧列表与滑块步骤 ----
        frames = []                                 # plotly 动画帧
        slider_steps = []                           # 滑块步骤(每个对应一帧的稳定状态)
        for k, p in enumerate(idxs):                # 遍历区间内每一帧
            d = decomps[k]                          # 当前帧的分解结果
            bars = self._build_bars(d["bid"], d["ask"])                 # 6 条柱状图 trace
            shapes = self._divider_shapes(d["bid"]) + self._divider_shapes(d["ask"])  # 分割线
            if k == 0:                              # ---- 区间首帧: 单帧, 无变化动画 ----
                trig = self.loader.get_trigger(p)   # 首帧的触发信息(可能为 None)
                ttype = trig["triggerType"] if trig is not None else "无(初始帧)"
                ann = self._annotation(f"第 {p} 帧　|　起始状态　|　触发类型: {ttype}")
                fr_name = f"f{p}"                   # 帧名称
                frames.append(go.Frame(
                    name=fr_name, data=bars,
                    layout=go.Layout(xaxis=dict(range=list(d["xrange"])),
                                     shapes=shapes, annotations=[ann]),
                ))
                slider_steps.append(self._slider_step(fr_name, str(p)))  # 滑块步骤
            else:                                   # ---- 后续帧: 拆成 change + shift 两个动画帧 ----
                row = self.loader.get_frame(p)      # 当前帧原始行
                trig = self.loader.get_trigger(p)   # 当前帧触发信息
                ttype = trig["triggerType"] if trig is not None else "未知"
                ann = self._annotation(
                    f"第 {p} 帧　|　adjIndex={int(row['adjIndex'])}　|　"
                    f"时间 {_format_time(row['time'])}　|　触发类型: {ttype}")
                prev_xrange = decomps[k - 1]["xrange"]      # 上一帧的 x 轴范围
                # 变化阶段: 数据已是当前帧, 但 x 轴范围仍停留在上一帧(位置先不动)
                frames.append(go.Frame(
                    name=f"f{p}c", data=bars,
                    layout=go.Layout(xaxis=dict(range=list(prev_xrange)),
                                     shapes=shapes, annotations=[ann]),
                ))
                # 平移阶段: 数据相同, x 轴范围平移到当前帧的盘口位置
                frames.append(go.Frame(
                    name=f"f{p}s", data=bars,
                    layout=go.Layout(xaxis=dict(range=list(d["xrange"])),
                                     shapes=shapes, annotations=[ann]),
                ))
                slider_steps.append(self._slider_step(f"f{p}s", str(p)))  # 滑块对准稳定态

        # ---- 播放 / 暂停按钮 ----
        play_btn = dict(label="▶ 播放", method="animate", args=[None, dict(
            frame=dict(duration=frame_duration, redraw=True),       # 每帧停留时长, redraw 以刷新分割线
            transition=dict(duration=transition_duration),          # 帧间过渡动画时长
            fromcurrent=True, mode="immediate")])
        pause_btn = dict(label="⏸ 暂停", method="animate", args=[[None], dict(
            frame=dict(duration=0, redraw=False),
            transition=dict(duration=0), mode="immediate")])

        # ---- 用首帧的数据与布局创建初始图 ----
        first = frames[0]                           # 首个动画帧
        fig = go.Figure(data=list(first.data), frames=frames)
        fig.update_layout(
            barmode="overlay",                      # 已手动堆叠, 用 overlay
            title=dict(text="订单簿动态回放", x=0.5, font=dict(size=16)),
            xaxis=dict(title="价格", range=list(decomps[0]["xrange"])),
            yaxis=dict(title="挂单量(手)", range=[0, y_max]),
            shapes=list(first.layout.shapes),                   # 初始分割线
            annotations=list(first.layout.annotations),         # 初始文字标注
            legend=dict(orientation="h", y=-0.2),               # 图例横向置底
            updatemenus=[dict(type="buttons", direction="left", showactive=False,
                              x=0.02, y=1.16, xanchor="left", yanchor="top",
                              buttons=[play_btn, pause_btn])],
            sliders=[dict(active=0, x=0.12, len=0.86, y=0, pad=dict(t=10),
                          currentvalue=dict(prefix="当前帧位置: "), steps=slider_steps)],
            plot_bgcolor="white", width=1000, height=620,
        )
        return fig

    @staticmethod
    def _slider_step(frame_name, label):
        """构造一个滑块步骤, 点击后立即跳转到指定动画帧."""
        return dict(method="animate", label=label, args=[[frame_name], dict(
            mode="immediate", frame=dict(duration=0, redraw=True),
            transition=dict(duration=0))])


# 当作为脚本直接运行时, 生成一份默认的 toy 数据
if __name__ == "__main__":
    generate_toy_data()
