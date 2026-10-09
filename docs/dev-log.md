# 麦麦省粮管家 · 开发过程记录

> 参赛要求：用 WorkBuddy 开发时，保存完整开发对话（MD 格式）。本文档为开发全流程记录。

## 项目信息

- 项目名：麦麦省粮管家（单人午餐预算优化助手）
- 赛道：麦当劳中国 × WorkBuddy「1024 程序员节创意开发大赛」
- 开发工具：腾讯 WorkBuddy
- 开发时间：2026-10-09

## 选题背景

大赛要求基于麦当劳 MCP 能力平台开发创意 Skill。官方示例方向含「麦麦省钱助手」。结合个人后端/算法背景，选择「省钱」这一贴近真实需求的场景，并把技术深度放在「券组合最优化算法」上，而非简单的工具调用。

## 开发步骤

### Step 1 — 研读 MCP 能力文档

读取 `https://open.mcd.cn/mcp/doc`，梳理出约 31 个可用工具（详见接入文档），确定省钱场景所需的核心工具：

- `auto-bind-coupons`：一键领券
- `available-coupons` / `query-store-coupons`：查询可用券
- `query-nearby-stores`：定位门店
- `query-meals` / `query-meal-detail`：菜单与餐品
- `list-nutrition-foods`：营养数据（支撑控卡约束）
- `calculate-price` / `create-order`：计价与下单

### Step 2 — 设计优化算法

核心问题抽象为：**给定「可选餐品 + 可用券 + 预算/营养约束」，求「优惠最多/实付最低」的餐品与券组合**。

技术要点：
1. 券统一建模（满减 / 折扣 / 代金券），叠加与互斥规则可插拔
2. 分支限界搜索餐品组合，位掩码子集枚举券组合
3. 双目标策略：`max_saving`（省最多）/ `min_pay`（实付最低）

### Step 3 — 实现并验证

`src/optimizer.py` 实现算法，用离线示例数据验证：

- 输入：巨无霸/板烧鸡腿堡/中薯条/中杯可乐/麦乐鸡 + 满30减8/满40减12/9折券
- 输出：`板烧鸡腿堡 + 中杯可乐 + 麦乐鸡4块 ｜ 满40减12 ｜ 原价 ¥44 省 ¥12 实付 ¥32`

### Step 4 — 接入真实 MCP 数据并修正算法

配置 `mcd-mcp` 后实测，推翻第一版「满减券」假设：

- `query-meals`：餐品带 `currentPrice`/`originalPrice`，`discountType` 标注优惠类型（促销/麦金卡/随单购）。省钱核心是**促销价差**。
- `query-store-coupons`：券是「指定商品特价券」，绑定 `products[].productCode`，非金额满减券。
- `calculate-price`：金额单位是「分」，需 /100 转元。

据此新增 `optimize_promo()`（0/1 背包 + 分支限界，目标「价差最大」）和 `mcp_adapter.py`（真实 JSON → 模型映射）。

**端到端验证**（武汉光谷门店真实数据）：
- 预算 40 省最多：美禄可可雪冰 + 人气超值四件套，划线价 ¥80 → 实付 ¥37.9，省 ¥42.1

### Step 5 — 封装为 Skill

编写 `SKILL.md`，定义触发场景与执行流程，接入 WorkBuddy。

### Step 6 — 接入营养数据（控卡能力）

`list-nutrition-foods` 实测返回 158 条餐品营养数据（能量/蛋白/脂肪/碳水/钠/钙），存为 `data/nutrition.tsv`。

- `mcp_adapter.py` 新增 `load_nutrition` / `backfill_nutrition`，按餐品名精确/包含匹配回填热量与蛋白
- 发现真实限制：套餐类（三件套/四件套）营养表无独立条目，只能模糊匹配到单品
- 语义修正：`FoodItem` 加 `nutrition_known` 标记，控卡/蛋白约束下自动跳过营养未知餐品

**端到端验证**：
- 控卡 500kcal + 蛋白 20g：麦辣鸡腿汉堡三件套，划线价 ¥46 → 实付 ¥33.5（485kcal/24g 蛋白）

### Step 7 — 接入真实报价（calculate-price）

实测 `calculate-price`，澄清省钱机制：

- 所有金额字段单位是「分」（originalPrice/price/discount/productList[].subtotal）
- `discount` 字段才是真实省钱额（如冰美式原价 ¥19 → 现价 ¥11.9，discount=710 分）
- 菜单划线价（originalPrice）只是营销标注，结算时并不产生实际优惠
- `takeWayList` 提供取餐方式 code（堂食 eat-in / 外带 take-in-store），下单需携带

据此新增 `parse_quote()`（分转元 + 报价摘要），并写 `demo.py` 端到端演示入口，全链路闭环。

### Step 8 — 对抗式审查与重构

对 SKILL 做第一性原理审查，发现并修复了核心缺陷：

**审查发现的 P0 问题**：
1. `optimize()` 里的满减券路径（`apply_coupons`/`coupon_subsets`）在真实数据下是死代码——菜单总有划线价，`has_promo` 恒真，永远走 `optimize_promo`，券被丢弃
2. 「省钱额」错误地用菜单划线价差，而非 `calculate-price` 的真实 `discount`
3. 券（指定商品特价券）根本未纳入优化

**重构方案**（一次到位）：
- 每个餐品建模为「多价格档位」：`PriceOption`（无券现价 + 券后价），券后价从券 title 提取（如「9.9元中杯冰美式」）
- 优化器统一为单一真实路径：遍历「商品 × 档位」组合，目标 = 真实省钱额最大
- 删除满减死代码，`Coupon` 模型废弃，改用 `PriceOption.coupon_id` 标记券占用
- 营养匹配修正：套餐类（三件套/四件套/随心）不再模糊匹配单品，避免「三件套」错误套用「单品」营养值
- SKILL 流程：领券改为显式触发（有副作用），补「无解兜底」

**重构后验证**：
- 冰美式 + 9.9元券：现价 ¥11.9 → 券后 ¥9.9（discount 从 710 分升到 910 分，券真实生效）
- 预算 40 省最多：冰美式(券) + 人气超值四件套，省 ¥38.1 实付 ¥37.9

### Step 9 — top-K 候选 + 真实报价精排

新增 `optimize_topk()`（返回 top-K 候选）和 `rerank_by_real_quote()`（对候选逐个调 calculate-price，用真实 discount 精排）。

**真实数据佐证（武汉光谷门店实测）**：
- 大堡口福三件套：菜单划线价 ¥47，但 `calculate-price` 返回 `discount=0`（划线价是假的，实际原价就是 ¥22.9）
- 美禄可可雪冰：`discount=1310 分`（划线价差 ¥13.1 是真的）

结论：**划线价完全不可信，不同商品 discount 表现各异，必须用真实报价精排才能保证省钱额正确**。

## 项目完成度

- [x] 菜单映射（parse_meals，多价格档位）
- [x] 券映射（parse_coupons + attach_coupon_options，券后价纳入优化）
- [x] 营养映射（backfill_nutrition，套餐不误匹配）
- [x] 组合优化（optimize，0/1 背包 + 分支限界，商品×档位）
- [x] 真实报价解析（parse_quote，分转元）
- [x] top-K 候选 + 真实报价精排（optimize_topk + rerank_by_real_quote）
- [x] 端到端演示（demo.py）

## 待办

- [ ] 发布到 GitHub 公开仓库并提交参赛 Issue
- [ ] （可选）在 Skill 层串联 create-order 实现真实下单

### Step 11 — 用户实测反馈 + 意图体验层

用户自测后发现 5 个产品问题，指向同一根因：**Skill 只会「省钱」，不懂「点餐」**。

用户反馈：
1. 大晚上推咖啡不合理
2. 套餐已含可乐，还重复推咖啡
3. 为凑券金额而凑单
4. 能否按历史喜好推荐
5. 应先询问用户意图（健康/解馋/换口味/其他）

**定位转变**：省钱从「目标」降级为「约束」，新增「点餐意图前置层」。

**新增 `src/taste.py`**：
- `current_slot()` / `filter_by_time()`：时段过滤（晚餐/夜宵不推咖啡、早餐类）
- `infer_category()`：餐品品类推断（套餐类优先匹配，避免「三件套」误判「汉堡」）
- `combo_containment_check()`：套餐品类去重（套餐含可乐就不再推咖啡/饮料）
- `is_filler_purchase()`：凑单保护（加购净支出上升 = 负优化）
- `intent_hint()`：五意图（健康/解馋/换口味/省钱/随便）推荐口径

**SKILL.md 重构**：流程改为「阶段 A 先问意图 → 阶段 B 带意图检索（含时段过滤）→ 阶段 C 优化+质量校验 → 阶段 D 会话内记忆」。`run.py` 集成时段过滤 + 套餐去重，返回 `slot/filter_note/warnings` 字段。

**历史喜好的现实**：MCP 无历史订单接口（仅 query-order 单条查询），历史喜好只能靠「会话内记忆 + 显式询问」，跨会话不保留。已在 SKILL 阶段 D 明确说明。

**验证**：晚餐 20:00 场景，咖啡/雪冰被正确过滤，推荐「人气超值四件套」而非咖啡组合。

### Step 10 — 第二轮独立审查与修复

启动独立审查子代理，用全新视角（不带开发记忆偏见）复核，发现并修复了以下问题：

**P1-1 堆比较崩溃（已修复）**：`optimize_topk()` 的堆元素 `(key, candidate)` 在候选键相等时会比较到 `Order`，触发 `TypeError`。修复：加自增 `seq` 作为第三比较键。已复现并验证修复。

**P0-2 流程断裂（已修复）**：SKILL 只说「喂给 optimize()」，但 MCP JSON → FoodItem 之间缺接线。新增 `src/run.py` 编排层，把「MCP → 映射 → 优化 → 精排 → 下单」串起来，SKILL.md 改为引用 `build_foods()`/`optimize_topk()`/`rerank_by_real_quote()`。

**P1-3 脆弱解析（已加固）**：`parse_meals` 兼容 meals 为 dict/list；`parse_coupons` 兼容 list 或 `{"data":[...]}`。

**P2-4 营养误配（已收紧）**：包含匹配仅当长度差 ≤2 且名称长度 ≥3，避免「可乐」误配「可乐中杯」。

**P2-6 文档矛盾（已修复）**：README「一键领券」与 SKILL「显式触发」统一；README 项目结构补 run.py。

**保留的已知限制（诚实声明）**：
- P1-2 套餐需 `roundList`/`query-meal-detail` 才能正确计价下单（当前 build_quote_items 不含 roundList）
- P1-5 非价格券（买一送一/随单购等）无法从 title 解析券价，会被跳过
- P1-6 top-K 精排是启发式近似，非全局最优
- P2-1 所谓「分支限界」实为约束剪枝的全枚举，餐品多时性能有限
