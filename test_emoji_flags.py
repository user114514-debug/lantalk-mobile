# -*- coding: utf-8 -*-
"""utils/emoji_flags 纯解析逻辑单测。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.emoji_flags import (
    has_flag, iter_segments, tokenize_text, flag_code, is_regional_indicator)

CN = "\U0001F1E8\U0001F1F3"   # 🇨🇳
US = "\U0001F1FA\U0001F1F8"   # 🇺🇸
JP = "\U0001F1EF\U0001F1F5"   # 🇯🇵


def check(name, cond):
    if not cond:
        raise AssertionError("FAIL: " + name)
    print("PASS:", name)


check("RI detection", is_regional_indicator(CN[0]) and not is_regional_indicator("A"))
check("flag_code CN", flag_code(CN) == "CN")
check("flag_code US", flag_code(US) == "US")
check("flag_code JP", flag_code(JP) == "JP")

check("has_flag true", has_flag("你好" + CN) is True)
check("has_flag false plain", has_flag("hello 世界") is False)
check("has_flag single RI false", has_flag("x" + CN[0] + "y") is False)

segs = list(iter_segments("a" + CN + "b" + US))
kinds = [(k, v) for k, v in segs]
check("segments structure",
      kinds == [("text", "a"), ("flag", "CN"), ("text", "b"), ("flag", "US")])

segs2 = list(iter_segments(CN))
check("single flag segment", segs2 == [("flag", "CN")])

# 单个未成对 RI 保留为文本
segs3 = list(iter_segments("x" + CN[0]))
check("lone RI kept as text", segs3 == [("text", "x" + CN[0])])

# 分词：中文逐字、英文按词、空白独立
toks = tokenize_text("你好 world")
check("tokenize mixed", toks == ["你", "好", " ", "world"])

toks2 = tokenize_text("hello  world")
check("tokenize spaces", toks2 == ["hello", "  ", "world"])

toks3 = tokenize_text("汉字")
check("tokenize cjk", toks3 == ["汉", "字"])

# 国旗 PNG 资源存在
flags_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "flags")
check("cn.png exists", os.path.exists(os.path.join(flags_dir, "cn.png")))
check("us.png exists", os.path.exists(os.path.join(flags_dir, "us.png")))
n_png = len([f for f in os.listdir(flags_dir) if f.endswith(".png")])
check("flag png count >= 250", n_png >= 250)

print("\nALL EMOJI FLAG TESTS PASSED")
