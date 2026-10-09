"""
麦麦省粮管家 · 编排层

把「调 MCP → 数据映射 → 组合优化 → 真实报价精排 → 下单」串成可执行流程。

在 WorkBuddy 中，本模块由 Skill 按流程调用；各 MCP 工具通过 mcd-mcp 提供。
本文件定义了每一步的输入输出，使 agent 能明确地串联整条链路。

注意：本模块依赖 mcd-mcp 工具（运行时注入），离线时可用 demo.py 演示。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from mcp_adapter import (
    attach_coupon_options,
    backfill_nutrition,
    build_quote_items,
    load_nutrition,
    parse_coupons,
    parse_meals,
    rerank_by_real_quote,
)
from optimizer import FoodItem, optimize_topk
from taste import (
    check_completeness,
    combo_containment_check,
    current_slot,
    explain_filter,
    filter_by_time,
    intent_hint,
)


def build_foods(
    meals_payload: dict,
    store_coupons: list,
    *,
    nutrition_path: Optional[str] = "data/nutrition.tsv",
) -> list[FoodItem]:
    """把菜单 + 门店券 + 营养数据 组装成优化器输入（多价格档位）。

    meals_payload : query-meals 返回的 data（含 meals 映射表）
    store_coupons : query-store-coupons 返回的 data（券列表）
    """
    foods = parse_meals(meals_payload)
    coupons = parse_coupons(store_coupons)
    foods = attach_coupon_options(foods, coupons)
    if nutrition_path:
        foods = backfill_nutrition(foods, load_nutrition(nutrition_path))
    return foods


def run(
    meals_payload: dict,
    store_coupons: list,
    quote_fn: Callable,
    *,
    budget: Optional[float] = None,
    max_energy: Optional[float] = None,
    min_protein: Optional[float] = None,
    max_items: int = 3,
    objective: str = "max_saving",
    topk: int = 10,
    nutrition_path: Optional[str] = "data/nutrition.tsv",
    intent: Optional[str] = None,
    now=None,
    require_complete_meal: bool = True,
) -> Optional[dict]:
    """执行完整推荐流程，返回最优方案 + 真实报价摘要 + 质量说明。

    quote_fn(order) -> calculate-price 的原始 payload dict。
    intent：用户点餐意图（健康/解馋/换口味/省钱/随便），影响推荐口径。
    require_complete_meal：默认 True，保证推荐是一顿完整餐（主食+饮品）。
    流程：时段过滤 → 组装 foods → 启发式 top-K → 真实报价精排 → 质量校验。
    """
    # 1. 时段过滤（晚餐/夜宵不推咖啡、早餐）
    slot = current_slot(now)
    foods_raw = build_foods(
        meals_payload, store_coupons, nutrition_path=nutrition_path
    )
    foods, dropped = filter_by_time(foods_raw, slot)
    if not foods:
        return None

    # 2. 意图提示（供 agent 组织推荐话术）
    hint = intent_hint(intent) if intent else intent_hint("随便")

    # 3. 启发式 top-K（默认要求「主食+饮品」完整餐）
    candidates = optimize_topk(
        foods,
        budget=budget,
        max_energy=max_energy,
        min_protein=min_protein,
        max_items=max_items,
        objective=objective,
        k=topk,
        require_complete_meal=require_complete_meal,
    )
    if not candidates:
        return None

    # 4. 真实报价精排
    best, quote = rerank_by_real_quote(candidates, quote_fn, objective=objective)
    if best is None:
        return None

    # 5. 质量校验（套餐去重 + 完整性）
    problems = combo_containment_check(best)
    problems += check_completeness(best)

    return {
        "order": best,
        "quote": quote,
        "items": build_quote_items(best),
        "slot": slot.name,
        "intent_hint": hint,
        "filter_note": explain_filter(dropped),
        "warnings": problems,
    }


if __name__ == "__main__":
    print("编排层 run.py：请在 WorkBuddy 中由 Skill 调用，")
    print("或运行 python3 src/demo.py 查看离线完整演示。")
