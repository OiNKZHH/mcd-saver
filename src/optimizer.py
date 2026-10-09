"""
麦麦省粮管家 · 省钱组合最优化核心模块

职责：给定「可选餐品（各带候选价格）+ 预算/营养约束」，求解「真实省钱额最大」的组合。

第一性原理（实测结论）：
1. 麦当劳的真实省钱额 = calculate-price 的 discount 字段（单位分），
   而非菜单划线价差。菜单 originalPrice 只是营销标注。
2. 券是「指定商品特价券」，作用是把特定商品的 productPrice 压到券后价
   （实测：冰美式现价 1190 分 → 用券后 990 分，discount 从 710 变为 910）。
3. 因此每个餐品是一组「候选价格」：无券现价 + 若干券后价。
   优化 = 在预算/营养约束内，选「商品 × 价格档位」组合，使真实省钱额最大。

算法：0/1 背包 + 分支限界。商品数通常 < 200、候选价 1~3 档，
组合空间可控；用预算剪枝 + 营养剪枝控制搜索。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceOption:
    """餐品的一个价格档位。

    pay:       实付价（元）。无券时 = 现价；用券时 = 券后价。
    saving:    该档位对应的真实省钱额（元），由 calculate-price 的 discount 得出。
    label:     该档位的说明（如「现价」「9.9元券」）。
    coupon_id: 使用该档位所需的券 ID；无券为 None。
    """
    pay: float
    saving: float
    label: str
    coupon_id: Optional[str] = None


@dataclass(frozen=True)
class FoodItem:
    """单个餐品（来自 query-meals + query-store-coupons）"""
    code: str
    name: str
    original_price: float           # 无优惠原价（分转元后）
    options: tuple[PriceOption, ...]  # 候选价格档位（至少一个：无券现价）
    energy_kcal: float = 0.0
    protein_g: float = 0.0
    nutrition_known: bool = False


@dataclass
class Order:
    """一次下单方案"""
    items: list[FoodItem]
    chosen_options: list[PriceOption]
    coupon_ids: list[str]
    original_price: float = 0.0
    discount: float = 0.0
    pay_price: float = 0.0
    energy_kcal: float = 0.0
    protein_g: float = 0.0

    def describe(self) -> str:
        names = " + ".join(i.name for i in self.items)
        labels = " + ".join(o.label for o in self.chosen_options)
        return (
            f"{names} ｜ {labels} ｜ "
            f"原价 ¥{self.original_price:.1f} 省 ¥{self.discount:.1f} "
            f"实付 ¥{self.pay_price:.1f}"
        )


# ---------------------------------------------------------------------------
# 优化器
# ---------------------------------------------------------------------------

def optimize(
    foods: list[FoodItem],
    *,
    budget: Optional[float] = None,
    max_energy: Optional[float] = None,
    min_protein: Optional[float] = None,
    max_items: int = 3,
    objective: str = "max_saving",
) -> Optional[Order]:
    """求解最优组合。

    约束（全部可选）：
    - budget:      实付金额上限
    - max_energy:  能量上限（kcal）
    - min_protein: 蛋白质下限（g）
    - max_items:   最多选几个单品

    目标（objective）：
    - "max_saving": 真实省钱额最大；省钱额相同时实付更低优先（默认）
    - "min_pay":    实付最低；实付相同时省钱额更大优先
    """
    best: Optional[Order] = None

    def better(candidate: Order) -> bool:
        if best is None:
            return True
        if objective == "max_saving":
            if candidate.discount != best.discount:
                return candidate.discount > best.discount
            return candidate.pay_price < best.pay_price
        if candidate.pay_price != best.pay_price:
            return candidate.pay_price < best.pay_price
        return candidate.discount > best.discount

    def dfs(
        start: int,
        chosen_items: list[FoodItem],
        chosen_opts: list[PriceOption],
        used_coupons: list[str],
    ) -> None:
        nonlocal best
        pay = round(sum(o.pay for o in chosen_opts), 2)
        if budget is not None and pay > budget:
            return
        energy = sum(i.energy_kcal for i in chosen_items)
        if max_energy is not None and energy > max_energy:
            return
        if chosen_items:
            protein = sum(i.protein_g for i in chosen_items)
            if min_protein is None or protein >= min_protein:
                candidate = Order(
                    items=list(chosen_items),
                    chosen_options=list(chosen_opts),
                    coupon_ids=list(used_coupons),
                    original_price=round(
                        sum(i.original_price for i in chosen_items), 2
                    ),
                    discount=round(sum(o.saving for o in chosen_opts), 2),
                    pay_price=pay,
                    energy_kcal=energy,
                    protein_g=protein,
                )
                if better(candidate):
                    best = candidate
        for i in range(start, len(foods)):
            if len(chosen_items) >= max_items:
                break
            f = foods[i]
            # 控卡/蛋白约束下，营养未知的餐品无法保证达标，跳过
            if (max_energy is not None or min_protein is not None) \
                    and not f.nutrition_known:
                continue
            for opt in f.options:
                # 同一张券不重复使用
                if opt.coupon_id and opt.coupon_id in used_coupons:
                    continue
                chosen_items.append(f)
                chosen_opts.append(opt)
                if opt.coupon_id:
                    used_coupons.append(opt.coupon_id)
                dfs(i + 1, chosen_items, chosen_opts, used_coupons)
                chosen_items.pop()
                chosen_opts.pop()
                if opt.coupon_id:
                    used_coupons.pop()

    dfs(0, [], [], [])
    return best


def optimize_topk(
    foods: list[FoodItem],
    *,
    budget: Optional[float] = None,
    max_energy: Optional[float] = None,
    min_protein: Optional[float] = None,
    max_items: int = 3,
    objective: str = "max_saving",
    k: int = 10,
) -> list[Order]:
    """返回启发式下的 top-K 候选组合（按 objective 排序）。

    用途：优化器用「券后价差」做启发式初筛，得到 K 个候选后，
    由调用方对每个候选调 calculate-price 拿真实 discount 做精排。
    """
    import heapq

    heap: list[tuple] = []  # 元素 (key, seq, candidate)，seq 保证不比较到 Order
    seq = 0

    def push(candidate: Order) -> None:
        nonlocal seq
        # 定义「更差」的比较键，使堆顶始终是最差候选
        if objective == "max_saving":
            key = (candidate.discount, -candidate.pay_price)
        else:
            key = (-candidate.pay_price, candidate.discount)
        seq += 1
        heapq.heappush(heap, (key, seq, candidate))
        if len(heap) > k:
            heapq.heappop(heap)

    def dfs(
        start: int,
        chosen_items: list[FoodItem],
        chosen_opts: list[PriceOption],
        used_coupons: list[str],
    ) -> None:
        pay = round(sum(o.pay for o in chosen_opts), 2)
        if budget is not None and pay > budget:
            return
        energy = sum(i.energy_kcal for i in chosen_items)
        if max_energy is not None and energy > max_energy:
            return
        if chosen_items:
            protein = sum(i.protein_g for i in chosen_items)
            if min_protein is None or protein >= min_protein:
                push(
                    Order(
                        items=list(chosen_items),
                        chosen_options=list(chosen_opts),
                        coupon_ids=list(used_coupons),
                        original_price=round(
                            sum(i.original_price for i in chosen_items), 2
                        ),
                        discount=round(sum(o.saving for o in chosen_opts), 2),
                        pay_price=pay,
                        energy_kcal=energy,
                        protein_g=protein,
                    )
                )
        for i in range(start, len(foods)):
            if len(chosen_items) >= max_items:
                break
            f = foods[i]
            if (max_energy is not None or min_protein is not None) \
                    and not f.nutrition_known:
                continue
            for opt in f.options:
                if opt.coupon_id and opt.coupon_id in used_coupons:
                    continue
                chosen_items.append(f)
                chosen_opts.append(opt)
                if opt.coupon_id:
                    used_coupons.append(opt.coupon_id)
                dfs(i + 1, chosen_items, chosen_opts, used_coupons)
                chosen_items.pop()
                chosen_opts.pop()
                if opt.coupon_id:
                    used_coupons.pop()

    dfs(0, [], [], [])
    # 按 objective 降序输出最优在前
    if objective == "max_saving":
        return sorted(
            [c for _, _, c in heap],
            key=lambda c: (c.discount, -c.pay_price),
            reverse=True,
        )
    return sorted(
        [c for _, _, c in heap],
        key=lambda c: (-c.pay_price, c.discount),
        reverse=True,
    )

if __name__ == "__main__":
    foods = [
        FoodItem(
            "507411", "麦咖啡™冰美式中杯", 19.0,
            options=(
                PriceOption(11.9, 7.1, "现价"),
                PriceOption(9.9, 9.1, "9.9元券", coupon_id="C_AMER"),
            ),
            energy_kcal=13.0, protein_g=1.0, nutrition_known=True,
        ),
        FoodItem(
            "9900004236", "人气超值四件套随心选", 57.0,
            options=(PriceOption(28.0, 29.0, "现价"),),
            energy_kcal=0.0, protein_g=0.0, nutrition_known=False,
        ),
        FoodItem(
            "9900015602", "大堡口福三件套", 47.0,
            options=(PriceOption(22.9, 24.1, "现价"),),
            energy_kcal=0.0, protein_g=0.0, nutrition_known=False,
        ),
    ]
    r = optimize(foods, budget=40, max_items=2, objective="max_saving")
    print("省最多：", r.describe() if r else "无解")
    r2 = optimize(foods, budget=40, max_items=1, objective="min_pay")
    print("实付最低：", r2.describe() if r2 else "无解")
