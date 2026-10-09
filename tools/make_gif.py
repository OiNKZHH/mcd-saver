"""
生成 README 用演示 GIF（终端打字机效果）

产出 docs/demo.gif：模拟 MCD Saver 的推荐过程，
突出「先问意图 → 时段过滤 → 套餐去重」的「会点餐」逻辑。

用法：/path/to/venv/bin/python tools/make_gif.py
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

# 终端配色（暗色终端风格，对比度高）
BG = (30, 30, 30)
FG = (235, 235, 235)
ACCENT = (255, 199, 44)   # 麦当劳黄
GREEN = (120, 200, 120)
RED = (240, 120, 120)
CYAN = (120, 200, 220)
DIM = (150, 150, 150)

W, H = 720, 460
LINE_H = 30
PAD_X = 28
TOP = 20

# 演示台词（分帧，每帧追加一行）
LINES = [
    ("你：今晚想吃麦当劳，帮我点", FG),
    ("", FG),
    ("麦麦省粮管家：先问一句——", ACCENT),
    ("   你想「健康 / 解馋 / 换口味 / 省钱 / 随便」？", ACCENT),
    ("你：随便吧，能省钱最好", FG),
    ("", FG),
    ("[意图] 随便 → 按当前时段均衡搭配", DIM),
    ("[时段] 晚餐 20:00 → 过滤咖啡/早餐类", CYAN),
    ("  × 麦咖啡™冰美式中杯（咖啡，晚上不推）", RED),
    ("  × 美禄可可雪冰（咖啡，晚上不推）", RED),
    ("[套餐去重] 套餐已含饮料，不再重复加点", CYAN),
    ("", FG),
    ("[优化] 多价格档位组合 + 真实报价精排", DIM),
    ("  ● 人气超值四件套随心选  省 ¥29.0", GREEN),
    ("  ● 板烧鸡腿堡三件套      省 ¥12.5", GREEN),
    ("", FG),
    ("[结果] 实付 ¥28.0 · 省 ¥29.0", ACCENT),
    ("  不是硬凑的咖啡，是刚好的一顿。", FG),
]


def load_font(size: int):
    # 优先用系统中文等宽字体
    for p in [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ]:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main() -> None:
    font = load_font(18)
    font_big = load_font(22)

    frames = []
    # 逐帧追加行，制造打字机效果
    for end in range(1, len(LINES) + 1):
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)
        # 标题栏
        draw.rectangle([0, 0, W, 38], fill=(40, 40, 40))
        draw.text((PAD_X, 8), "麦麦省粮管家 · MCD Saver", font=font_big, fill=ACCENT)
        # 内容
        y = TOP + 30
        visible = LINES[:end]
        # 只显示最后 N 行（滚动），保持画面稳定
        MAX_VISIBLE = 14
        visible = visible[-MAX_VISIBLE:]
        for text, color in visible:
            draw.text((PAD_X, y), text, font=font, fill=color)
            y += LINE_H
        # 光标
        if visible:
            last = visible[-1][0]
            tw = draw.textlength(last, font=font)
            draw.rectangle([PAD_X + tw + 2, y - LINE_H + 3,
                            PAD_X + tw + 12, y - 3], fill=FG)
        frames.append(img)

    # 末尾多停留几帧
    frames += [frames[-1]] * 15

    frames[0].save(
        "docs/demo.gif",
        save_all=True,
        append_images=frames[1:],
        duration=320,
        loop=0,
    )
    print(f"已生成 docs/demo.gif（{len(frames)} 帧）")


if __name__ == "__main__":
    main()
