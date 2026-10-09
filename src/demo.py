"""
麦麦省粮管家 · 端到端演示

用离线样例数据完整演示整条链路：
菜单 → 券映射 → 营养回填 → 组合优化 → 真实报价解析

运行：python3 src/demo.py
无需 MCP Token 即可看到完整效果；接入 mcd-mcp 后替换为真实调用即可。
"""

from __future__ import annotations

from mcp_adapter import (
    attach_coupon_options,
    backfill_nutrition,
    build_quote_items,
    load_nutrition,
    parse_coupons,
    parse_meals,
    parse_quote,
    rerank_by_real_quote,
)
from optimizer import optimize, optimize_topk

# 样例菜单（字段结构与 query-meals 的 data.meals 一致）
SAMPLE_MEALS = {
    "9900005456": {"name": "麦辣鸡腿汉堡三件套", "currentPrice": "33.5", "originalPrice": "46"},
    "9900016076": {"name": "我就喜欢鸡薯双全盒", "currentPrice": "15.9", "originalPrice": "28.5"},
    "9900016078": {"name": "蘸酱麦麦脆汁鸡", "currentPrice": "11.9", "originalPrice": "18"},
    "9900016073": {"name": "龙焰鸡腿堡三件套", "currentPrice": "26.9", "originalPrice": "47.5"},
    "9900015602": {"name": "大堡口福三件套", "currentPrice": "22.9", "originalPrice": "47"},
    "521871": {"name": "美禄可可雪冰", "currentPrice": "9.9", "originalPrice": "23"},
    "9900004236": {"name": "人气超值四件套随心选", "currentPrice": "28", "originalPrice": "57"},
    "507411": {"name": "麦咖啡™冰美式中杯", "currentPrice": "11.9", "originalPrice": "19"},
}

# 样例券（字段结构与 query-store-coupons 的 data 一致）
SAMPLE_COUPONS = [
    {"couponId": "C_AMER", "title": "9.9元中杯冰美式",
     "products": [{"productCode": "507411"}]},
    {"couponId": "C_FRIES", "title": "薯薯任选",
     "products": [{"productCode": "9900016370"}]},
]

# 样例真实报价（字段结构与 calculate-price 的 data 一致，单位「分」）
SAMPLE_QUOTE = {
    "originalPrice": 1900,
    "discount": 910,
    "price": 990,
    "productList": [
        {"productCode": "507411", "productName": "麦咖啡™冰美式中杯",
         "quantity": 1, "originalSubtotal": 1900, "subtotal": 990}
    ],
    "takeWayList": [
        {"code": "eat-in", "title": "堂食"},
        {"code": "take-in-store", "title": "外带"},
    ],
}


def main() -> None:
    print("=" * 60)
    print("麦麦省粮管家 · 端到端演示")
    print("=" * 60)

    # 1. 菜单 → 模型（现价档位）
    foods = parse_meals({"meals": SAMPLE_MEALS})

    # 2. 券 → 追加券后价档位
    coupons = parse_coupons(SAMPLE_COUPONS)
    foods = attach_coupon_options(foods, coupons)
    n_opts = sum(len(f.options) for f in foods)
    print(f"\n[1] 解析菜单 {len(foods)} 个餐品，"
          f"共 {n_opts} 个价格档位（含券后价）")

    # 3. 营养回填
    nutrition = load_nutrition("data/nutrition.tsv")
    foods = backfill_nutrition(foods, nutrition)
    print(f"[2] 营养回填：已知 {sum(1 for f in foods if f.nutrition_known)} 个")

    # 4. 优化
    print("\n[3] 组合优化（预算 40，省最多）：")
    r = optimize(foods, budget=40, max_items=2, objective="max_saving")
    print("   ", r.describe())

    print("\n[4] 组合优化（预算 20，实付最低）：")
    r2 = optimize(foods, budget=20, max_items=2, objective="min_pay")
    print("   ", r2.describe())

    # 5. 真实报价解析
    print("\n[5] 真实报价解析（calculate-price，分转元）：")
    q = parse_quote(SAMPLE_QUOTE)
    print(f"    原价 ¥{q['original']} → 实付 ¥{q['pay']}（省 ¥{q['saving']}）")
    print(f"    取餐方式：{' / '.join(t['title'] for t in q['take_ways'])}")

    # 6. top-K 候选 → 真实报价精排（模拟 quote_fn）
    print("\n[6] top-K 候选 + 真实报价精排：")
    topk = optimize_topk(foods, budget=40, max_items=2,
                         objective="max_saving", k=5)

    # 模拟 calculate-price：真实省钱额按商品真实 discount 计算。
    # 数据来自实测（武汉光谷门店）：
    #   冰美式(券)    discount=910分  (省 9.1)
    #   四件套        discount=2900分 (省 29.0)
    #   大堡口福三件套 discount=0分    (划线价¥47是假的，实际原价就是¥22.9)
    #   美禄可可雪冰  discount=1310分 (省 13.1，划线价差是真的)
    REAL_DISCOUNT = {  # productCode -> 真实 discount（元）
        "507411": 9.1,
        "9900004236": 29.0,
        "9900015602": 0.0,   # 划线价虚高，真实无优惠
        "521871": 13.1,
    }

    def mock_quote(order):
        items = build_quote_items(order)
        pay = 0
        saving = 0
        orig = 0
        plist = []
        for it, opt in zip(order.items, order.chosen_options):
            real_saving = REAL_DISCOUNT.get(it.code, opt.saving)
            real_pay = round(it.original_price - real_saving, 2)
            pay += real_pay
            saving += real_saving
            orig += it.original_price
            plist.append({
                "productCode": it.code, "productName": it.name,
                "quantity": 1,
                "subtotal": int(real_pay * 100),
                "originalSubtotal": int(it.original_price * 100),
            })
        return {
            "data": {
                "originalPrice": int(orig * 100),
                "discount": int(saving * 100),
                "price": int(pay * 100),
                "productList": plist,
                "takeWayList": [{"code": "eat-in", "title": "堂食"}],
            }
        }

    best, best_q = rerank_by_real_quote(topk, mock_quote)
    print(f"    启发式 top1: {topk[0].describe()}")
    print(f"    真实报价最优: {best.describe()}")
    print(f"    真实省钱额: ¥{best_q['saving']} 实付 ¥{best_q['pay']}")
    print("    说明：大堡口福三件套启发式「省¥24.1」，但真实 discount=0（划线价虚高），")
    print("         精排阶段会被 calculate-price 的真实 discount 纠正。")

    # 7. 意图与时段过滤（体验规则层）
    from taste import (
        combo_containment_check,
        current_slot,
        filter_by_time,
        explain_filter,
        intent_hint,
    )
    from datetime import datetime
    print("\n[7] 意图与时段过滤（体验规则层）：")
    for intent in ("健康", "解馋", "换口味", "省钱", "随便"):
        print(f"    · {intent}: {intent_hint(intent)}")
    slot = current_slot(datetime(2026, 10, 9, 20, 0))
    kept, dropped = filter_by_time(foods, slot)
    print(f"    时段[{slot.name}]过滤: {explain_filter(dropped) or '无排除'}")
    print(f"    保留 {len(kept)} 个候选餐品")
    warnings = combo_containment_check(best)
    print(f"    套餐去重校验: {warnings or '无重复消费'}")

    print("\n" + "=" * 60)
    print("演示完成。接入 mcd-mcp 后替换为真实调用即可。")
    print("=" * 60)


if __name__ == "__main__":
    main()
