# 项目计划 — visualizeLOB

## 摘要
简要说明：以Python和plotly为工具，构建一个订单簿（Limit Order Book, LOB）动态可视化工具，用于展示买卖挂单随时间逐帧变化的回放视图。

**目标与范围**
- 数据输入：
    - orderbook.parquet: 订单簿的每次变化的结果（买卖10档以内的价格和剩余挂单量）
    - triggerInfo.parquet: 引起订单簿发生变化的那笔行情（order或cancel，其中order可能引起交易也可能只是挂单）
- 可视化当前买卖10档的深度、导致当前订单簿状态的逐笔行情（挂单、撤单、成交）、微观结构动画
- 提供交互（缩放、过滤、时间控制）

---

## 里程碑（Milestones）
- M1 — toy数据的生成(orderbook.parquet和triggerInfo.parquet)
- M2 — 数据输入接口（配置orderbook.parquet和triggerInfo.parquet的地址，并框定日期、票号、起始时间(或订单簿的某个index)、结束时间）
- M3 — 基础可视化（显示某一帧的情况：订单簿的样子，和上一帧的区别（导致这一帧变化的原因））
- M4 — 动态可视化（范围内逐帧的显示）
- M5 — 文档与示例数据，发布

---

## 细节

### 数据细节

- orderbook.parquet 包含的列有:
  - code: 股票代码
  - adjIndex: 任意一帧的订单簿的索引，（code, adjIndex）双元组可以精确定位到当天的任意票的某个状态
  - time: 对应的交易所的时间
  - serverTime: 对应的本地收到行情的时间
  - bid(ask)Px1 -> bid(ask)Px10， 买(卖)盘的1档到10档价格
  - bid(ask)Vlm1 -> bid(ask)Vlm10， 买(卖)盘的1档到10档挂单量

- triggerInfo.parquet 包含的列有:
  - code: 股票代码
  - adjIndex: 导致了订单簿变化的行情的索引，可以通过（code, adjIndex）双元组索引merge到orderbook.parquet上面
  - triggerType: order或cancel；事实上市场上只有这两种行情，成交其实也是order引起的

### 业务逻辑细节

- triggerInfo文件里面并没有指定任何的价量信息，只有triggerType，具体的价量信息是通过比较当前的orderbook状态和上一个orderbook状态的区别得出的
- 相邻的orderbook帧之间，绝大多数情况下变化是很小的，只有bidPx1或者askPx1会变化，甚至都不变。最复杂的情况就是两个都变，这是因为发生了partial fill的情况，比如一个主动的大卖单，吃掉了买盘的若干档，然后自己还剩了一部分，成为了新的卖一。必须想清楚所有的case和coner case，处理好它们。
- 两个输入数据源是怎么来的呢？其实是拿历史逐笔数据在本地重新自己合成了订单簿（交易所只发逐笔，不发逐帧orderbook，但是有了逐笔就足够100%还原交易所的orderbook里面发生的所有事情了），然后每当orderbook发生变化，我就分别记录新的一行到orderbook.parquet和triggerInfo.parquet里。
- 废话一句，交易所里会为每个code维护一个orderbook
- 在可视化的时候，orderbook的变化要展示出来，比如增加的部分(挂单或partial fill)要颜色深一点，消失的部分（cancel或成交）要颜色淡一点，蓝色代表买盘，红色代表卖盘
- 可视化orderbook请使用柱状图，x轴是价格，y轴是量
- toy文件可以生成在一个文件夹里，但是请务必注意，生成的toy文件必须是符合逻辑的！！！（关于符合逻辑，指的是符合订单簿的工作原理。举个例子，比如买二突然少了量，那只能是撤单，不可能是交易，因为不可能出现跳过买一去和买二成交；再举个例子，相邻的两帧orderbook，不可能出现买一和买二同时增加挂单量，因为挂的都是固定价格的限价单）
- 订单簿的规则是非常简单的，只有两种操作，一是挂限价单（可能导致3个瞬时结果，零成交、部分成交和全成交），二是撤单（可以撤掉完全没成交的单子，也可以撤掉部分成交的单子）；成交的规则也是按照通用的价格优先，时间优先的规则

### 最终的呈现细节

- 我想要你用Python实现，可以用pandas，numpy,scipy，plotly， matplotlib，jupyterlab，os这几个库。然后所有的class和函数都实现在一个py文件里
- 再给一个使用它的demo，写在一个jupyterlab文件里面，import这个工具，还有相应的东西，读入toy数据，然后展示
- 展示的内容就所很简单，提供可交互的plotly界面
- 请用uv创建一个新的环境，安装需要的那几个库
- 请务必给每行代码都写上详细的注释