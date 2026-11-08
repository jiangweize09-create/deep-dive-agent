---
skill: channel-analysis
display_name: 渠道分析
triggers: [渠道分析, 渠道对比, 渠道表现, 各渠道, 渠道下滑]
applicable: 渠道规模与效率对比类问题，必须结合数据
data_hint: 必须先查数——五渠道的规模、效率、结构性交叉
output_template: 渠道对比表 + 「规模最大/增长最快/效率最高」三结论
---

# SOP：渠道分析

适用场景：「各渠道表现 / 渠道对比 / 某端流量下滑」类问题。
目标：对五个渠道做规模与效率对比，给出结构性结论。

## 渠道清单

channels 表五个渠道：iOS App、Android App、小程序、Web、H5。
orders.channel_id 关联 channels.id。演示数据渠道权重约为 32/28/22/12/6。

## 分析步骤

1. 规模对比：各渠道 GMV 与订单量（status='completed'），按月看趋势更稳。
   占比图用环形图（generate_chart chart_type=pie）。
2. 效率对比：各渠道客单价（SUM(amount)/COUNT(*)）与付费用户数
   （COUNT(DISTINCT user_id)）。App 端客单价通常高于 H5。
3. 渠道 × 品类交叉：orders JOIN products JOIN categories，
   看小程序是否偏低价品类、App 是否偏数码电子等结构性差异。
4. 渠道 × 会员等级：orders JOIN users，各渠道新客的 level 构成。

## 常见陷阱

- 渠道占比只看单月会被大促扭曲（大促常在特定渠道投放），用近 3~6 个月均值。
- H5 基数小，占比波动大，比较时给绝对量而不是只给百分比。
- 渠道分析经常被用来回答「渠道下滑」，记得连到 GMV 下滑 SOP 的流量路径。

## 输出要求

- 表格：渠道、GMV、订单量、客单价、付费用户数、GMV 占比。
- 结论给出「规模最大 / 增长最快 / 效率最高」三个渠道各一个。
