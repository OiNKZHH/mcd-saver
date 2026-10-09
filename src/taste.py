"""
麦麦省粮管家 · 点餐意图与体验规则层

职责：在「纯省钱优化」之上，叠加「会点餐」的产品逻辑。省钱是约束，不是目标。

解决的问题（来自用户实测反馈）：
1. 时段适配：大晚上不推咖啡/早餐类餐品
2. 套餐品类去重：套餐已含可乐，不再额外推咖啡/饮料
3. 凑单保护：为凑券金额而加购、但净支出反而上升的组合，视为负优化
4. 意图导向：先理解用户「为什么点」（健康/解馋/换口味/省钱），再带意图推荐

设计原则：
- 本模块不碰 optimizer 的算法，只做「优化前过滤候选 + 优化后质量校验」
- 品类推断基于 query-meals 返回的 categories（分类名）与餐品 name 关键词
- 所有规则可配置、可解释（每条过滤都给出 reason，便于 agent 向用户解释）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# 品类与时段
# ---------------------------------------------------------------------------

# 时段定义
@dataclass(frozen=True)
class TimeSlot:
    name: str
    start: time
    end: time


MORNING = TimeSlot("早餐", time(5, 0), time(10, 30))
LUNCH = TimeSlot("午餐", time(10, 30), time(14, 0))
AFTERNOON = TimeSlot("下午茶", time(14, 0), time(17, 0))
DINNER = TimeSlot("晚餐", time(17, 0), time(21, 0))
LATE = TimeSlot("夜宵", time(21, 0), time(23, 59))
LATE_MID = TimeSlot("深夜", time(0, 0), time(5, 0))

SLOTS = [LATE_MID, MORNING, LUNCH, AFTERNOON, DINNER, LATE]


def current_slot(now: Optional[datetime] = None) -> TimeSlot:
    """根据当前时间判断时段。"""
    now = now or datetime.now()
    t = now.time()
    for s in SLOTS:
        if s.start <= t < s.end or (s.name == "深夜" and t < time(5, 0)):
            return s
    return LATE


# 品类关键词（用于从 name/category 推断餐品类别）
CATEGORY_RULES: dict[str, tuple[str, ...]] = {
    "咖啡": ("咖啡", "美式", "拿铁", "卡布奇诺", "玛奇朵", "阿芙佳朵", "麦咖啡", "雪冰"),
    "早餐": ("麦满分", "早安", "粥", "油条", "豆浆", "薯饼"),
    "饮料": ("可乐", "雪碧", "橙", "柠檬红茶", "奶茶", "牛奶", "燕麦奶", "茶", "苹果汁", "怡泉"),
    "薯条小食": ("薯条", "薯饼", "玉米杯", "脆薯饼", "薯薯"),
    "甜品": ("麦旋风", "新地", "派", "冰淇淋", "圆筒", "雪冰", "拉明顿"),
    "汉堡": ("汉堡", "牛堡", "鸡腿堡", "鳕鱼堡", "吉士", "巨无霸", "板烧", "卷"),
    "炸鸡": ("炸鸡", "麦乐鸡", "脆汁鸡", "鸡翅", "鸡块", "鸡排", "鸡柳", "V翅"),
    "套餐": ("套餐", "件套", "四件套", "三件套", "随心配", "随心选", "分享餐", "拼盘"),
}


def infer_category(name: str) -> str:
    """根据餐品名推断类别。

    优先级：套餐类关键词优先（套餐/件套/随心配/随心选/分享餐），
    避免「麦辣鸡腿汉堡三件套」被误判为「汉堡」。
    """
    # 套餐类优先
    combo_kw = ("套餐", "件套", "四件套", "三件套", "随心配", "随心选", "分享餐", "拼盘", "双人餐")
    if any(kw in name for kw in combo_kw):
        return "套餐"
    for cat, keywords in CATEGORY_RULES.items():
        if cat == "套餐":
            continue
        for kw in keywords:
            if kw in name:
                return cat
    return "其他"


# 时段 → 不适合的品类（用于过滤）
TIME_EXCLUSION: dict[str, tuple[str, ...]] = {
    "早餐": (),                 # 早餐时段什么都不排除
    "午餐": ("早餐",),           # 午餐不推早餐类
    "下午茶": ("早餐",),
    "晚餐": ("早餐", "咖啡"),    # 晚餐不推咖啡、早餐
    "夜宵": ("早餐", "咖啡"),    # 夜宵不推咖啡、早餐
    "深夜": ("早餐", "咖啡"),
}


# ---------------------------------------------------------------------------
# 意图
# ---------------------------------------------------------------------------

INTENTS = ("健康", "解馋", "换口味", "省钱", "随便")


def intent_hint(intent: str) -> str:
    """给定意图，返回一句引导 agent 的推荐口径。"""
    hints = {
        "健康": "优先推荐 500 大卡套餐、低脂高蛋白、含蔬菜的选项，默认加控卡约束",
        "解馋": "优先推荐炸鸡、甜品、汉堡等「过瘾」类，不太在意热量",
        "换口味": "避免推荐历史点过的品类，主动提新品（tags 带「上新」的）",
        "省钱": "以真实省钱额最大为目标，允许凑单，但仍遵守时段与去重规则",
        "随便": "按当前时段推荐一个均衡搭配（主食 + 小食/饮料），再按省钱微调",
    }
    return hints.get(intent, hints["随便"])


# ---------------------------------------------------------------------------
# 过滤与校验
# ---------------------------------------------------------------------------

def filter_by_time(
    foods: list,
    slot: TimeSlot,
    *,
    get_name=lambda f: f.name,
) -> list:
    """按时段过滤掉不适合的品类。返回 (保留列表, 过滤说明列表)。"""
    excluded = TIME_EXCLUSION.get(slot.name, ())
    kept = []
    dropped = []
    for f in foods:
        cat = infer_category(get_name(f))
        if cat in excluded:
            dropped.append((get_name(f), cat))
        else:
            kept.append(f)
    return kept, dropped


def combo_containment_check(order) -> list[str]:
    """套餐品类去重校验：套餐已含的品类，订单里是否又重复加了。

    返回问题描述列表（空 = 无问题）。用于优化后质量校验。
    """
    problems = []
    combos = [i for i in order.items if infer_category(i.name) == "套餐"]
    singles = [i for i in order.items if infer_category(i.name) != "套餐"]
    if not combos:
        return problems
    # 套餐内已含的「饮料/小食」类别，若又单独点了同类别单品，则提示
    for s in singles:
        cat = infer_category(s.name)
        if cat in ("饮料", "咖啡", "薯条小食"):
            problems.append(
                f"套餐已含 {cat}，额外又点了「{s.name}」，可能是重复消费"
            )
    return problems


def is_filler_purchase(order, added_item, added_saving: float) -> bool:
    """凑单保护：判断「为凑单而加购」是否净负优化。

    added_item：为凑单新加的商品；added_saving：加购带来的额外省钱额。
    若加购成本 > 额外省钱，则为负优化（纯凑单）。
    """
    cost = min((o.pay for o in added_item.options), default=0.0)
    return cost > added_saving


def explain_filter(dropped: list) -> str:
    """把过滤结果变成一句可读说明。"""
    if not dropped:
        return ""
    parts = [f"{n}（{cat}）" for n, cat in dropped]
    return "已按当前时段排除：" + "、".join(parts)


# ---------------------------------------------------------------------------
# 餐的完整性（解决「只推吃的、没配喝的」）
# ---------------------------------------------------------------------------

# 一顿完整单人餐的品类结构：主食 + 饮品为「必备」，小食/甜品为「可选」
MEAL_STRUCTURE: dict[str, str] = {
    "汉堡": "主食",
    "炸鸡": "主食",
    "套餐": "主食",      # 套餐已含饮品，视为「主食+饮品」一体
    "早餐": "主食",
    "其他": "主食",      # 无法归类的主食类默认当主食
    "饮料": "饮品",
    "咖啡": "饮品",
    "薯条小食": "小食",
    "甜品": "甜品",
}

# 饮品大类（不含咖啡，因咖啡有时段限制）
DRINK_CATEGORIES = ("饮料", "咖啡")


def meal_roles(order) -> dict[str, bool]:
    """判断订单覆盖了哪些「餐的角色」（主食/饮品/小食/甜品）。

    套餐已含饮品，故套餐同时满足「主食」和「饮品」。
    """
    roles = {"主食": False, "饮品": False, "小食": False, "甜品": False}
    for i in order.items:
        cat = infer_category(i.name)
        role = MEAL_STRUCTURE.get(cat, "主食")
        roles[role] = True
        if cat == "套餐":
            # 套餐默认含饮品
            roles["饮品"] = True
    return roles


def check_completeness(order) -> list[str]:
    """检查一顿餐是否完整：必须有主食 + 饮品。

    返回缺失提示列表（空 = 完整）。用于推荐后的补全建议。
    """
    roles = meal_roles(order)
    problems = []
    if not roles["主食"]:
        problems.append("缺主食")
    if not roles["饮品"]:
        problems.append("缺饮品（建议配一杯饮料）")
    return problems


def completeness_gap(order) -> Optional[str]:
    """返回需要补的品类名（None 表示已完整）。优先补饮品。"""
    roles = meal_roles(order)
    if not roles["主食"]:
        return "主食"
    if not roles["饮品"]:
        return "饮品"
    return None
