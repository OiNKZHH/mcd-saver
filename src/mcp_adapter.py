"""
麦当劳 MCP 真实数据 → 优化器数据模型 的映射层。

第一性原理（实测结论）：
1. query-meals 的 currentPrice/originalPrice：currentPrice 是现价，originalPrice 是营销划线价。
2. query-store-coupons 的券是「指定商品特价券」，绑定 products[].productCode，
   作用是把该商品压到券后价（券价常藏在券 title 里，如「9.9元中杯冰美式」）。
3. calculate-price 的 discount（分）才是真实省钱额。

本模块把上述数据规整为优化器需要的 FoodItem（含多个候选价格档位）。
"""

from __future__ import annotations

import re
from typing import Any

from optimizer import FoodItem, PriceOption


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_meals(meals_payload: dict) -> list[FoodItem]:
    """把 query-meals 的 data 映射为「仅含无券现价档位」的 FoodItem 列表。

    现价的真实省钱额 = 真实原价 - 现价，需 calculate-price 复核；
    此处先用「菜单划线价差」做启发式占位，最终省钱额以报价为准。

    单位说明：query-meals 的 currentPrice/originalPrice 实测为「元」
    （如 "33.5"），与 calculate-price 的「分」不同，本函数不做单位换算。
    """
    items: list[FoodItem] = []
    meal_map = meals_payload.get("meals", {})
    # 兼容 meals 为 dict(code→meal) 或 list[{code, name, ...}]
    if isinstance(meal_map, list):
        meal_map = {m.get("code"): m for m in meal_map if m.get("code")}
    for code, m in meal_map.items():
        cur = _to_float(m.get("currentPrice"))
        orig = _to_float(m.get("originalPrice"))
        if cur <= 0:
            continue
        name = m.get("name", code)
        # 启发式省钱额 = max(划线价差, 0)，真实值由 calculate-price 复核
        heuristic_saving = max(0.0, orig - cur)
        items.append(
            FoodItem(
                code=code,
                name=name,
                original_price=orig if orig > 0 else cur,
                options=(PriceOption(cur, heuristic_saving, "现价"),),
                energy_kcal=0.0,
                protein_g=0.0,
                nutrition_known=False,
            )
        )
    return items


def parse_coupons(coupons_payload) -> list[dict]:
    """解析 query-store-coupons，返回「券 → 绑定商品 + 券价提示」的列表。

    每条：{coupon_id, title, product_codes, price_hint}
    price_hint：从券 title 提取的券后价（如「9.9元」→ 9.9），无则 None。

    兼容入参为 list 或 {"data": [...]} 两种结构。
    """
    if isinstance(coupons_payload, dict):
        coupons_payload = coupons_payload.get("data", [])
    result = []
    for c in coupons_payload:
        title = c.get("title", "")
        codes = [
            p.get("productCode")
            for p in c.get("products", [])
            if p.get("productCode")
        ]
        # 从 title 提取「X.X元」券价
        m = re.search(r"(\d+(?:\.\d+)?)\s*元", title)
        price_hint = float(m.group(1)) if m else None
        result.append(
            {
                "coupon_id": c.get("couponId", ""),
                "title": title,
                "product_codes": codes,
                "price_hint": price_hint,
            }
        )
    return result


def attach_coupon_options(
    foods: list[FoodItem],
    coupons: list[dict],
) -> list[FoodItem]:
    """把券后价作为额外候选档位拼进对应 FoodItem。

    规则：券绑定的商品 code 若匹配某 FoodItem，则为该商品追加一个「券后价」档位。
    券后价 = 券 title 里的 price_hint；若无 price_hint，则跳过（无法确定券后价）。
    """
    by_code = {f.code: f for f in foods}
    out: dict[str, list[PriceOption]] = {f.code: list(f.options) for f in foods}

    for c in coupons:
        hint = c.get("price_hint")
        if hint is None:
            continue
        for code in c.get("product_codes", []):
            if code in out:
                base = by_code[code]
                opt = PriceOption(
                    pay=hint,
                    saving=max(0.0, base.original_price - hint),
                    label=c["title"],
                    coupon_id=c["coupon_id"],
                )
                # 券后价更低才值得作为候选
                if hint < base.options[0].pay:
                    out[code].append(opt)

    return [
        FoodItem(
            code=f.code,
            name=f.name,
            original_price=f.original_price,
            options=tuple(out[f.code]),
            energy_kcal=f.energy_kcal,
            protein_g=f.protein_g,
            nutrition_known=f.nutrition_known,
        )
        for f in foods
    ]


def parse_quote(payload: dict) -> dict[str, Any]:
    """解析 calculate-price 返回，产出「真实报价」摘要（分转元）。

    关键：discount（分）是真实省钱额，originalPrice 是真实无优惠原价。
    """
    d = payload.get("data", payload)

    def yuan(fen) -> float:
        try:
            return round(float(fen) / 100, 2)
        except (TypeError, ValueError):
            return 0.0

    products = [
        {
            "code": p.get("productCode"),
            "name": p.get("productName"),
            "quantity": p.get("quantity"),
            "subtotal": yuan(p.get("subtotal")),
            "originalSubtotal": yuan(p.get("originalSubtotal")),
        }
        for p in d.get("productList", [])
    ]
    take_ways = [
        {"code": t.get("code"), "title": t.get("title")}
        for t in d.get("takeWayList", [])
    ]
    return {
        "original": yuan(d.get("originalPrice")),
        "pay": yuan(d.get("price")),
        "saving": yuan(d.get("discount")),
        "products": products,
        "take_ways": take_ways,
    }


def build_quote_items(order) -> list[dict]:
    """把 Order 转成 calculate-price 的 items 入参。

    order：optimizer.Order。每个商品带其选中的券（PriceOption.coupon_id）。
    """
    items = []
    for item, opt in zip(order.items, order.chosen_options):
        entry: dict[str, Any] = {"productCode": item.code, "quantity": 1}
        if opt.coupon_id:
            entry["couponId"] = opt.coupon_id
        items.append(entry)
    return items


def rerank_by_real_quote(
    candidates,
    quote_fn,
    *,
    objective: str = "max_saving",
):
    """对 top-K 候选用真实报价精排，返回 (最优候选, 真实报价摘要)。

    quote_fn(order) -> calculate-price 的原始 payload dict。
    用 payload 的 data.discount（真实省钱额）重新比较。
    """
    best = None
    best_quote = None
    for c in candidates:
        try:
            payload = quote_fn(c)
        except Exception:
            continue
        q = parse_quote(payload)
        # 真实省钱额 = q["saving"]，实付 = q["pay"]
        if best is None:
            best, best_quote = c, q
            continue
        if objective == "max_saving":
            if q["saving"] > best_quote["saving"] or (
                q["saving"] == best_quote["saving"] and q["pay"] < best_quote["pay"]
            ):
                best, best_quote = c, q
        else:
            if q["pay"] < best_quote["pay"] or (
                q["pay"] == best_quote["pay"] and q["saving"] > best_quote["saving"]
            ):
                best, best_quote = c, q
    return best, best_quote


def load_nutrition(path: str) -> dict[str, tuple[float, float]]:
    """加载营养数据 TSV，返回 {餐品名: (energy_kcal, protein_g)}。"""
    result: dict[str, tuple[float, float]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            try:
                result[parts[0]] = (float(parts[1]), float(parts[2]))
            except ValueError:
                continue
    return result


def backfill_nutrition(
    foods: list[FoodItem],
    nutrition: dict[str, tuple[float, float]],
) -> list[FoodItem]:
    """把营养数据回填到 FoodItem（精确匹配优先，其次受限包含匹配）。

    注意：
    - 套餐类餐品营养表无独立条目，不做模糊匹配，保持 nutrition_known=False。
    - 包含匹配仅当「营养表名是 menu 名的严格子串」且长度足够接近时才采用，
      避免「可乐」误配「可乐中杯」这类规格错配。
    """
    out: list[FoodItem] = []
    for f in foods:
        kcal, protein = nutrition.get(f.name, (0.0, 0.0))
        known = kcal > 0.0 or protein > 0.0
        is_combo = "套餐" in f.name or "件套" in f.name or "随心" in f.name
        if not known and not is_combo and len(f.name) >= 3:
            for nname, (k, p) in nutrition.items():
                # 仅当营养表名是 menu 名的前缀/后缀子串，且长度差不超过 2
                if len(nname) < 3:
                    continue
                if (nname in f.name or f.name in nname) and \
                        abs(len(nname) - len(f.name)) <= 2:
                    kcal, protein = k, p
                    known = True
                    break
        out.append(
            FoodItem(
                code=f.code,
                name=f.name,
                original_price=f.original_price,
                options=f.options,
                energy_kcal=kcal,
                protein_g=protein,
                nutrition_known=known,
            )
        )
    return out
