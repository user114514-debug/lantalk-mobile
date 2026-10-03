# client/config.py - 客户端配置
import json
import os
import time
import base64

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9999
DEFAULT_IP_MODE = "auto"  # auto / ipv4 / ipv6，默认自动优先IPv4

import sys
if getattr(sys, 'frozen', False):
    _BASE = os.path.dirname(sys.executable)
else:
    _BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(_BASE, "client_config.json")

def load_server_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("server_host", SERVER_HOST), cfg.get("server_port", SERVER_PORT)
        except Exception:
            pass
    return SERVER_HOST, SERVER_PORT

def load_server_ipv6():
    """加载保存的 IPv6 地址（单独一栏，不和 IPv4 host 混存）。"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("server_ipv6", "")
        except Exception:
            pass
    return ""

def save_server_config(host, port, ipv6=None):
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["server_host"] = host
    data["server_port"] = port
    if ipv6 is not None:
        data["server_ipv6"] = ipv6
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_ip_mode():
    """加载IP版本模式：auto / ipv4 / ipv6，默认auto。"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                mode = json.load(f).get("ip_mode", DEFAULT_IP_MODE)
                if mode in ("auto", "ipv4", "ipv6"):
                    return mode
        except Exception:
            pass
    return DEFAULT_IP_MODE


def save_ip_mode(mode):
    """保存IP版本模式到配置文件。"""
    if mode not in ("auto", "ipv4", "ipv6"):
        mode = DEFAULT_IP_MODE
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["ip_mode"] = mode
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_language():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("language", "zh")
        except Exception:
            pass
    return "zh"

def save_language(lang):
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["language"] = lang
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============ 记住登录（7天） ============
REMEMBER_DAYS = 7  # 令牌有效期 7 天

def save_remember_me(username, password):
    """勾选'记住我'并登录成功后调用：存账号+密码(base64)+7天过期时间。"""
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["remember_user"] = username
    data["remember_pass"] = base64.b64encode(password.encode("utf-8")).decode("ascii")
    data["remember_expire"] = time.time() + REMEMBER_DAYS * 86400
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_remember_me():
    """返回 (username, password) 或 None（未勾选/已过期）。"""
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        user = cfg.get("remember_user", "")
        pass_b64 = cfg.get("remember_pass", "")
        expire = cfg.get("remember_expire", 0)
        if not user or not pass_b64:
            return None
        if time.time() > expire:
            return None  # 已过期
        pwd = base64.b64decode(pass_b64.encode("ascii")).decode("utf-8")
        return user, pwd
    except Exception:
        return None

def clear_remember_me():
    """退出登录时清除记住的账号密码。"""
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in ("remember_user", "remember_pass", "remember_expire"):
            data.pop(k, None)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
