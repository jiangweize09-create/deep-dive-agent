# 指标口径手册

本手册是 Deep Dive 语义层（metrics.yaml）的口径说明。回答任何指标问题前必须先对齐口径；
数字与口径不一致时，以本手册为准。

## GMV（商品交易总额）

- 英文名 gmv，同义词：成交额、交易额、销售额、交易总额、营收。
- 定义：**已完成订单**的金额总和，不含退款、待支付与已取消订单。
- 表达式：`SUM(orders.amount)`，必带过滤 `orders.status = 'completed'`。
- 统计粒度：订单（order）。可用维度：month / region / channel / category。
- 常见错误：忘记带 status 过滤会把退款单算进去，数值偏高约 10%~20%。

## 订单量（order_count）

- 同义词：订单数、单量、成交笔数。
- 定义：**已完成订单**的笔数。表达式 `COUNT(*)`，必带 `orders.status = 'completed'`。
- 统计粒度：订单。可用维度：month / region / channel / category。
- 注意：全状态订单数（含 pending/refunded/cancelled）如需统计，必须显式说明
  「按状态拆解」或「全状态」，不能默认混用。

## 客单价（aov）

- 同义词：平均订单金额、每单价格、人均消费。
- 定义：平均每笔**已完成**订单的金额 = GMV / 订单量。
- 表达式：`SUM(orders.amount) / COUNT(*)`，必带 `orders.status = 'completed'`。
- 口径陷阱：客单价 ≠ 用户人均消费（后者按去重用户算，是 active_users 维度的口径）。

## 退款率（refund_rate）

- 同义词：退款比例、退款占比。
- 定义：已退款订单金额 / **全部状态**订单金额。基于所有状态订单计算，
  required_filters 为空——这一指标**不要**加 status='completed' 过滤。
- 表达式：`SUM(CASE WHEN orders.status = 'refunded' THEN orders.amount ELSE 0 END) / SUM(orders.amount)`。

## 付费用户数（active_users）

- 同义词：购买用户数、活跃用户、下单用户数。
- 定义：时间范围内有**已完成订单**的去重用户数。
- 表达式：`COUNT(DISTINCT orders.user_id)`，必带 `orders.status = 'completed'`。
- 统计粒度：订单表去重，可用维度 month / region / channel。

## 新增用户数（new_users）

- 同义词：新注册用户、新用户、注册用户。
- 定义：时间范围内新注册用户数，基于 `users.register_date` 统计，**与订单状态无关**。
- 表达式：`COUNT(users.id)`，required_filters 为空。
- 注意：新增用户 ≠ 新客（首单用户）。首单用户口径需基于订单表首次下单时间计算。

## 时间口径通用约定

- 所有时间区间**左闭右开**：`时间字段 >= start AND 时间字段 < end`。
- 「上个月 / 近 N 个月」指最近 N 个**完整自然月，不含本月**；
  「近 N 天」含今天往前共 N 个自然日。以 resolve_date_range 工具返回值为准。
- 金额单位为元（DECIMAL），展示时保留到整数或两位小数。
