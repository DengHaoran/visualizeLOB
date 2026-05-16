# -*- coding: utf-8 -*-
"""test_visualize_lob.py — visualizeLOB 的冒烟测试与逻辑自洽性校验.

本脚本无需 pytest, 直接运行即可:
    uv run python test_visualize_lob.py
全部通过时进程退出码为 0, 任一断言失败时退出码为 1.

覆盖范围:
    1. InternalOrderBook —— 撮合(价格优先/时间优先)与撤单
    2. generate_toy_data —— 数据 schema 与订单簿逻辑自洽性
    3. LOBDataLoader     —— 读取、过滤、逐帧/触发访问
    4. LOBVisualizer     —— 静态单帧图与动画图的结构
"""

import os                                      # 用于路径拼接
import sys                                     # 用于设置进程退出码
import tempfile                                # 用于把 toy 数据写到临时目录
import shutil                                  # 用于清理临时目录

import pandas as pd                            # 用于读取与校验 parquet

import visualize_lob as vl                     # 被测模块


# 累计通过的断言数, 仅用于结尾汇总
_passed = 0


def check(condition, message):
    """轻量断言: 条件为真则记一次通过, 否则抛出 AssertionError."""
    global _passed
    assert condition, f"[失败] {message}"      # 条件不成立直接抛错
    _passed += 1                               # 记一次通过
    print(f"  [通过] {message}")               # 打印通过信息


def _maps(row):
    """把 orderbook 的一行拆成买/卖盘的 {价格: 量} 字典(供一致性校验使用)."""
    bid, ask = {}, {}                          # 买盘、卖盘字典
    for i in range(1, 11):                     # 遍历 1~10 档
        bp, bv = row[f"bidPx{i}"], row[f"bidVlm{i}"]   # 买 i 档价与量
        if pd.notna(bp) and bv > 0:                    # 该档有效
            bid[round(float(bp), 4)] = float(bv)
        ap, av = row[f"askPx{i}"], row[f"askVlm{i}"]   # 卖 i 档价与量
        if pd.notna(ap) and av > 0:                    # 该档有效
            ask[round(float(ap), 4)] = float(av)
    return bid, ask


def test_internal_order_book():
    """测试 InternalOrderBook 的撮合与撤单逻辑."""
    print("[1] InternalOrderBook 撮合与撤单")
    ob = vl.InternalOrderBook(tick=0.01)               # 新建空订单簿

    # 空簿挂买单 -> 应作为挂单留存
    ob.place_limit_order("buy", 10.00, 100)            # 挂 100 手买单 @10.00
    check(ob.best_bid() == 10.00, "买单挂入后买一价为 10.00")
    bids, asks = ob.snapshot()                         # 取快照
    check(bids == [(10.00, 100)], "买一档量为 100")
    check(asks == [], "卖盘为空")

    # 卖单部分成交: 卖 30 手 @10.00, 吃掉买单的 30 手
    ob.place_limit_order("sell", 10.00, 30)            # 主动卖单
    bids, _ = ob.snapshot()                            # 取快照
    check(bids == [(10.00, 70)], "部分成交后买一档剩余 70 手")

    # 卖单全部成交并剩余挂单: 卖 100 手 @10.00, 吃掉剩余 70 手, 余 30 手挂为卖单
    ob.place_limit_order("sell", 10.00, 100)           # 主动卖单
    check(ob.best_bid() is None, "买盘被吃空, 买一价为 None")
    check(ob.best_ask() == 10.00, "余量挂为卖单, 卖一价为 10.00")
    _, asks = ob.snapshot()                            # 取快照
    check(asks == [(10.00, 30)], "卖一档量为剩余的 30 手")

    # 时间优先: 同价位先挂的单先成交
    ob2 = vl.InternalOrderBook(tick=0.01)              # 新建订单簿
    ob2.place_limit_order("buy", 20.00, 10)            # 先挂的买单(10 手)
    ob2.place_limit_order("buy", 20.00, 40)            # 后挂的买单(40 手)
    ob2.place_limit_order("sell", 20.00, 10)           # 主动卖单, 应吃掉"先挂的 10 手"
    check(ob2.snapshot()[0] == [(20.00, 40)], "时间优先: 先挂的单先成交, 仅余后挂的 40 手")

    # 撤单
    ob3 = vl.InternalOrderBook(tick=0.01)              # 新建订单簿
    ob3.place_limit_order("buy", 5.00, 10)             # 挂一笔买单
    oid = list(ob3.orders.keys())[0]                   # 取该订单的订单号
    check(ob3.cancel_order(oid) is True, "撤单返回 True")
    check(ob3.best_bid() is None, "撤单后买盘为空")
    check(ob3.cancel_order(oid) is False, "重复撤单返回 False")


def test_generate_toy_data():
    """测试 generate_toy_data 的输出 schema 与订单簿逻辑自洽性, 返回临时目录路径."""
    print("[2] generate_toy_data 数据 schema 与逻辑自洽性")
    tmp = tempfile.mkdtemp(prefix="lobtest_")          # 创建临时目录
    ob_df, tr_df = vl.generate_toy_data(out_dir=tmp, n_events=100, seed=2026)  # 生成数据

    # ---- schema 检查 ----
    check(len(ob_df) == 101, "orderbook 共 101 帧(1 初始 + 100 变化)")
    check(len(tr_df) == 100, "triggerInfo 共 100 个事件")
    check(str(ob_df["code"].dtype) == "int64", "code 列为整数")
    check(str(ob_df["adjIndex"].dtype) == "int64", "adjIndex 列为整数")
    check(set(tr_df["triggerType"]) <= {"order", "cancel"},
          "triggerType 仅含 order / cancel")
    check(os.path.exists(os.path.join(tmp, "orderbook.parquet")), "orderbook.parquet 已写出")
    check(os.path.exists(os.path.join(tmp, "triggerInfo.parquet")), "triggerInfo.parquet 已写出")

    # ---- time / serverTime 为 HHMMSSmmm 整数 ----
    check(str(ob_df["time"].dtype) == "int64", "time 列为整数")
    check(str(ob_df["serverTime"].dtype) == "int64", "serverTime 列为整数")
    for col in ("time", "serverTime"):
        for t in ob_df[col]:                       # 逐个校验编码合法性
            h, m, s, ms3 = t // 10**7, (t // 10**5) % 100, (t // 10**3) % 100, t % 1000
            assert 0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59 and 0 <= ms3 <= 999, \
                f"{col} 值 {t} 不是合法的 HHMMSSmmm 编码"
    check(True, "time / serverTime 均为合法的 HHMMSSmmm 编码")
    tval = ob_df["time"].tolist()                  # time 序列
    check(all(tval[i] < tval[i + 1] for i in range(len(tval) - 1)), "time 逐帧严格递增")
    check((ob_df["serverTime"] >= ob_df["time"]).all(), "每行 serverTime 不早于 time")

    # ---- adjIndex 单调递增且允许跳号; (code, adjIndex) 唯一 ----
    ai = ob_df["adjIndex"].tolist()                    # adjIndex 序列
    check(all(ai[i] < ai[i + 1] for i in range(len(ai) - 1)), "adjIndex 单调递增")
    check(any(ai[i + 1] - ai[i] > 1 for i in range(len(ai) - 1)), "adjIndex 存在跳号")
    check(not ob_df.duplicated(["code", "adjIndex"]).any(), "(code, adjIndex) 唯一")

    # ---- triggerInfo 都能 merge 回 orderbook ----
    merged = tr_df.merge(ob_df[["code", "adjIndex"]], on=["code", "adjIndex"], how="left",
                         indicator=True)
    check((merged["_merge"] == "both").all(), "全部触发事件都能关联到 orderbook")

    # ---- 逐帧结构: 买价递减、卖价递增、买一<卖一 ----
    trig = {(r.code, r.adjIndex): r.triggerType for r in tr_df.itertuples()}  # 触发查找表
    for i in range(len(ob_df)):
        row = ob_df.iloc[i]                            # 当前帧
        bpx = [row[f"bidPx{j}"] for j in range(1, 11) if pd.notna(row[f"bidPx{j}"])]
        apx = [row[f"askPx{j}"] for j in range(1, 11) if pd.notna(row[f"askPx{j}"])]
        assert all(bpx[k] > bpx[k + 1] for k in range(len(bpx) - 1)), "买价应严格递减"
        assert all(apx[k] < apx[k + 1] for k in range(len(apx) - 1)), "卖价应严格递增"
        if bpx and apx:                                # 两侧都有挂单
            assert bpx[0] < apx[0], "买一价必须低于卖一价(不允许交叉盘)"
    check(True, "每帧买价递减、卖价递增、买一<卖一")

    # ---- 相邻帧: 共有价格中挂单量上升的价格数; cancel 不应有增量 ----
    bad = 0                                            # 违反规则的次数
    for i in range(1, len(ob_df)):
        pb, pa = _maps(ob_df.iloc[i - 1])              # 上一帧
        cb, ca = _maps(ob_df.iloc[i])                  # 当前帧
        inc = sum(1 for side_p, side_c in [(pb, cb), (pa, ca)]
                  for p in set(side_p) & set(side_c) if side_c[p] > side_p[p] + 1e-9)
        ttype = trig.get((ob_df.iloc[i].code, ob_df.iloc[i].adjIndex))  # 该帧触发类型
        if inc > 1:                                    # 一次事件最多让 1 个价位增量
            bad += 1
        if ttype == "cancel" and inc > 0:              # 撤单不可能新增挂单量
            bad += 1
    check(bad == 0, "任一事件至多 1 个价位增量, 且 cancel 不带来增量")
    return tmp


def test_loader_and_visualizer(tmp):
    """测试 LOBDataLoader 与 LOBVisualizer."""
    print("[3] LOBDataLoader 读取与过滤")
    loader = vl.LOBDataLoader(os.path.join(tmp, "orderbook.parquet"),
                              os.path.join(tmp, "triggerInfo.parquet"))
    check(len(loader) == 101, "加载器读到 101 帧")
    check(loader.get_trigger(0) is None, "第 0 帧(初始帧)无触发信息")
    check(loader.get_trigger(5)["triggerType"] in ("order", "cancel"),
          "第 5 帧能取到触发类型")

    # 按 adjIndex 区间过滤
    lo = int(loader.get_frame(10)["adjIndex"])         # 第 10 帧的 adjIndex
    hi = int(loader.get_frame(30)["adjIndex"])         # 第 30 帧的 adjIndex
    loader.filter(start_index=lo, end_index=hi)        # 执行过滤
    check(len(loader) == 21, "过滤 [第10帧, 第30帧] 后得到 21 帧")
    loader.filter()                                    # 还原为全部数据
    check(len(loader) == 101, "无参数 filter() 还原为全部 101 帧")

    print("[4] LOBVisualizer 出图")
    viz = vl.LOBVisualizer(loader)                     # 创建可视化器
    check(abs(viz.tick - 0.01) < 1e-9, "自动推断 tick 为 0.01")

    fig0 = viz.plot_single_frame(0)                    # 初始帧静态图
    check(len(fig0.data) == 6, "单帧图含 6 条柱状 trace")

    fig8 = viz.plot_single_frame(8)                    # 第 8 帧静态图
    check(len(fig8.layout.shapes) >= 1, "有变化的帧含至少 1 条分割线")

    anim = viz.plot_animation()                        # 全程动画
    check(len(anim.frames) == 1 + 2 * 100, "动画含 201 帧(1 初始 + 100×2 阶段)")
    check(len(anim.layout.sliders[0].steps) == 101, "动画滑块含 101 个步骤")
    check(len(anim.layout.updatemenus[0].buttons) == 2, "动画含播放/暂停 2 个按钮")


def main():
    """依次运行全部测试, 并打印汇总结果."""
    tmp = None                                         # 临时目录路径
    try:
        test_internal_order_book()                     # 测试 1
        tmp = test_generate_toy_data()                 # 测试 2(返回临时目录)
        test_loader_and_visualizer(tmp)                # 测试 3 与 4
    finally:
        if tmp and os.path.isdir(tmp):                 # 清理临时目录
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n全部测试通过, 共 {_passed} 项断言。")     # 汇总


if __name__ == "__main__":
    try:
        main()                                         # 运行测试
    except AssertionError as e:                        # 有断言失败
        print(f"\n测试失败: {e}")
        sys.exit(1)                                    # 以非零码退出
    sys.exit(0)                                        # 全部通过
