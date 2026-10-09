# MCP 集成说明

本文档说明本项目实际使用的麦当劳 MCP 能力、调用流程及业务价值。

## MCP Server

| 项目 | 值 |
|---|---|
| Server 名称 | `mcd-mcp` |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 认证方式 | `Authorization: Bearer <MCP_TOKEN>` |
| MCP 版本 | 2025-06-18 及之前 |

## 实际使用的 Tool 与调用流程

本项目「麦麦省粮管家」按「意图优先，省钱是约束」的流程，分阶段调用以下 Tool：

### 阶段一：意图理解（无 Tool，纯对话）

先询问用户点餐意图：健康 / 解馋 / 换口味 / 省钱 / 随便。

### 阶段二：数据检索

| Tool | 用途 | 关键入参 | 关键出参 |
|---|---|---|---|
| `query-nearby-stores` | 查询附近门店 | `beType`(1到店/5得来速)、`searchType`、`city`、`keyword` | `storeCode`、`storeName`、`distance`、`reservation` |
| `query-meals` | 查询门店菜单 | `storeCode`、`orderType`、`beType` | `meals[].currentPrice`、`originalPrice`、`discountType`、`categories` |
| `query-store-coupons` | 查询门店可用券 | `storeCode`、`orderType`、`beType` | `couponId`、`couponCode`、`title`、`products[].productCode` |
| `list-nutrition-foods` | 查询营养数据（控卡场景） | 无 | `energyKcal`、`protein`、`fat`、`carbohydrate` |
| `available-coupons` | 查询可领取券 | 无 | 券标题、状态 |
| `auto-bind-coupons` | 一键领券（显式触发） | 无 | 领取结果、`couponId`/`couponCode` |

### 阶段三：组合优化（本地算法，无 Tool）

- 把菜单 + 券映射为「多价格档位」（无券现价 + 券后价）
- 时段过滤（晚餐/夜宵不推咖啡、早餐类）
- 套餐品类去重（套餐已含饮料不再重复加点）
- 0/1 背包 + 分支限界求解 top-K 候选

### 阶段四：真实报价与下单

| Tool | 用途 | 关键入参 | 关键出参 |
|---|---|---|---|
| `calculate-price` | 真实报价、精排 | `storeCode`、`orderType`、`beType`、`items[]`(含`productCode`/`quantity`/`couponId`) | `discount`(分)、`price`(分)、`originalPrice`(分)、`takeWayList` |
| `create-order` | 创建订单 | 门店、取餐方式、商品列表 | 订单详情、支付链接 |

## 关键技术发现

实测麦当劳 MCP 数据，得出三个驱动算法设计的结论：

1. **省钱额以 `calculate-price` 的 `discount` 为准，而非菜单划线价**。菜单 `originalPrice` 是营销标注（如「大堡口福三件套」划线价 ¥47，真实 `discount=0`）。
2. **券是「指定商品特价券」**，绑定 `products[].productCode`，把特定商品压到券后价（如「9.9元中杯冰美式」券，冰美式从 ¥11.9 → ¥9.9）。
3. **`calculate-price` 金额单位是「分」**，需 /100 转元。

## 业务价值

- **对用户**：从「只会算最便宜」升级为「先懂你为什么点」，推荐既合理又划算的一餐，避免「晚上推咖啡」「套餐含可乐还重复加咖啡」等反常识推荐。
- **对麦当劳**：把 MCP 的 30+ 能力组合成一个有真实业务闭环的 Skill，展示了「查询 → 决策 → 报价 → 下单」的完整链路，可作为「AI 点餐」场景的能力演示。

## 声明

本项目仅使用麦当劳 MCP 公开能力，未使用任何未授权接口或数据。Token 等敏感信息一律通过环境变量注入，不在代码或配置中硬编码。
