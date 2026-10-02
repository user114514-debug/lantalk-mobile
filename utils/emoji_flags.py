# -*- coding: utf-8 -*-
"""
Emoji（国旗）文本解析工具。

为什么需要它：
Flutter/Skia 对 regional-indicator（区域指示符，U+1F1E6–U+1F1FF）组成的
国旗序列字体回退有缺陷——普通文本字体里的字母字形会抢先认领国旗字符，
导致一面国旗显示成两个字母（如 CN / US），不应用彩色 emoji 字体的连字。
因此消息气泡遇到国旗时，需要把国旗序列切分出来，改用国旗 PNG 图片渲染。

本模块只做纯文本解析，不依赖 flet，便于单测。
"""

RI_LO = 0x1F1E6
RI_HI = 0x1F1FF


def is_regional_indicator(ch):
    return RI_LO <= ord(ch) <= RI_HI


def has_flag(text):
    """文本中是否存在相邻两个 regional indicator（即国旗序列）。"""
    prev = False
    for ch in text or "":
        cur = is_regional_indicator(ch)
        if cur and prev:
            return True
        prev = cur
    return False


def flag_code(pair):
    """两个 regional indicator -> ISO 3166 alpha-2 国家码，如 'CN'。"""
    a = ord(pair[0]) - RI_LO
    b = ord(pair[1]) - RI_LO
    return chr(65 + a) + chr(65 + b)


def iter_segments(text):
    """
    把文本切成有序片段：
      ('text', 普通字符串)
      ('flag', 'CN')  # 国家码
    单个、未成对的 regional indicator 作为普通文本保留。
    """
    i, n = 0, len(text or "")
    buf = []
    while i < n:
        ch = text[i]
        if is_regional_indicator(ch) and i + 1 < n and is_regional_indicator(text[i + 1]):
            if buf:
                yield ("text", "".join(buf))
                buf = []
            yield ("flag", flag_code(ch + text[i + 1]))
            i += 2
        else:
            buf.append(ch)
            i += 1
    if buf:
        yield ("text", "".join(buf))


def _is_cjk(ch):
    cp = ord(ch)
    return (
        0x3000 <= cp <= 0x303F or      # CJK 标点
        0x3040 <= cp <= 0x30FF or      # 平假名/片假名
        0x3400 <= cp <= 0x4DBF or      # 扩展 A
        0x4E00 <= cp <= 0x9FFF or      # 汉字
        0xF900 <= cp <= 0xFAFF or      # 兼容表意文字
        0xFF00 <= cp <= 0xFFEF or      # 全角字符
        0xAC00 <= cp <= 0xD7AF         # 韩文音节
    )


def tokenize_text(s):
    """
    把普通文本片段切成可在 Row(wrap=True) 里流式排列的最小 token：
      - CJK 字符：逐字切（中文/日文/韩文不需要词间空格）
      - 拉丁及其它字符：按空白切成词
      - 空白：原样作为独立 token（提供词间距）
    这样 Row 才能在窄气泡内自动换行。
    """
    tokens = []
    word = ""

    def flush():
        nonlocal word
        if word:
            tokens.append(word)
            word = ""

    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\n":
            flush()
            tokens.append("\n")
            i += 1
        elif ch.isspace():
            flush()
            j = i
            while j < n and s[j].isspace():
                j += 1
            tokens.append(s[i:j])
            i = j
        elif _is_cjk(ch):
            flush()
            tokens.append(ch)
            i += 1
        else:
            word += ch
            i += 1
    flush()
    return tokens
