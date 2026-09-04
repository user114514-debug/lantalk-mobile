# client/config.py - 客户端配置
import json
import os

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

def save_server_config(host, port):
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["server_host"] = host
    data["server_port"] = port
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
