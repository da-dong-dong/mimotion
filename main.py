#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
main.py
在 GitHub Actions 上运行：从 CONFIG 环境变量读取账号并提交步数。
支持两种账号配置：
1. 老模式：USER 和 PWD，用 # 分隔多账号
2. 新模式：ACCOUNTS 列表 [{"username": "...", "password": "..."}]
"""

import os
import json
import logging
import time
import base64
import hashlib
import random
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pytz
import re

# ---------- 日志 ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ---------- 常量 ----------
TARGET_STEP_API = "https://wzz.wangzouzou.com/motion/api/motion/Xiaomi"
DEFAULT_CONFIG = {
    "PUSH_PLUS_TOKEN": "",
    "USE_CONCURRENT": "false",
    "SLEEP_GAP": 5,
    "PUSH_PLUS_MAX": 30
}

# 时间段步数范围
STEP_RANGES = {
    8: (29988, 29999),
    12: (29988, 29999),
    14: (29988, 29999),
    16: (29988, 29999),
    22: (29988, 29999)
}

# ---------- 工具函数 ----------
def load_config_from_env():
    raw = os.environ.get("CONFIG", "").strip()
    if not raw:
        logger.error("CONFIG 环境变量为空。请在 GitHub Secrets 中添加 CONFIG 或使用老版 USER/PWD")
        return None
    try:
        cfg = json.loads(raw)
        return {**DEFAULT_CONFIG, **cfg}
    except Exception as e:
        logger.error(f"解析 CONFIG 异常：{e}")
        return None

def desensitize(user):
    u = str(user)
    if len(u) > 8:
        return f"{u[:3]}****{u[-4:]}"
    if len(u) > 2:
        return f"{u[0]}***{u[-1]}"
    return u

def push_plus(token, title, content):
    if not token:
        return
    try:
        url = "http://www.pushplus.plus/send"
        data = {"token": token, "title": title, "content": content, "template": "html"}
        resp = requests.post(url, data=data, timeout=10)
        if resp.ok and resp.json().get("code") == 200:
            logger.info("PushPlus 推送成功")
        else:
            logger.warning(f"PushPlus 推送失败：{resp.text}")
    except Exception as e:
        logger.error(f"PushPlus 推送异常：{e}")

def calc_step_for_now():
    now = datetime.now(pytz.timezone("Asia/Shanghai"))
    h = now.hour
    closest = min(STEP_RANGES.keys(), key=lambda x: abs(x - h))
    if abs(closest - h) <= 2:
        lo, hi = STEP_RANGES[closest]
        return random.randint(lo, hi)
    return random.randint(6000, 24000)

# ---------- 提交类 ----------
class StepSubmitter:
    def __init__(self, target_api=TARGET_STEP_API):
        self.s = requests.Session()
        self.target_api = target_api
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.7339.128 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://m.cqzz.top',
            'Referer': 'https://m.cqzz.top/',
            'X-Requested-With': 'XMLHttpRequest'
        }
        self.base_url = 'https://wzz.wangzouzou.com/motion/api/motion/Xiaomi'

    def insert_str(self, s: str, pos: int, insert_value) -> str:
        s = s or ""
        return s[:pos] + str(insert_value) + s[pos:]

    def mm(self, xmphone: str, xmpwd: str, time_ms: int) -> str:
        steps = ["4", "1", "0", "2", "3"]
        key = ""
        utc_time = datetime.utcfromtimestamp(time_ms / 1000.0)
        utc_month = utc_time.month - 1
        for step in steps:
            if step == "4":
                key = f"{xmphone}{str(time_ms)[8:13]}{xmpwd}"
            elif step == "1":
                key = base64.b64encode(key.encode("utf-8")).decode("utf-8")
            elif step == "0":
                key = self.insert_str(key, utc_time.hour, utc_month)
            elif step == "2":
                key = self.insert_str(key, utc_month, utc_time.hour)[7:27]
            elif step == "3":
                key = hashlib.md5(key.encode("utf-8")).hexdigest()
        return key

    def validate(self, username, password):
        if not username or not password:
            return False, "账号或密码为空"
        phone = r"^1[3-9]\d{9}$"
        email = r"^[\w\.-]+@[\w\.-]+\.\w+$"
        if re.match(phone, username) or re.match(email, username):
            return True, ""
        return False, "账号格式错误"

    def submit(self, username, password, steps):
        valid, msg = self.validate(username, password)
        if not valid:
            return False, msg
        try:
            time_val = int(time.time() * 1000)
            self.headers["time"] = str(time_val)
            self.headers["Authorization"] = self.mm(username, password, time_val)
            resp = self.s.post(
                self.target_api,
                data={"phone": username, "pwd": password, "num": steps},
                headers=self.headers,
                timeout=30
            )
            if not resp.ok:
                return False, f"HTTP错误: {resp.status_code}"
            result = resp.json()
            if result.get("code") == 200:
                return True, f"步数提交成功：{steps}"
            return False, f"接口错误：{result.get('data')}"
        except Exception as e:
            return False, f"请求异常：{e}"

# ---------- 主执行 ----------
def process_one(idx, total, account, submitter):
    user_disp = desensitize(account.get("username", ""))
    prefix = f"[{idx+1}/{total}] {user_disp}"
    try:
        steps = calc_step_for_now()
        ok, msg = submitter.submit(account.get("username"), account.get("password"), steps)
        logger.info(f"{prefix} - {'✅' if ok else '❌'} - {msg}")
        return {"user": user_disp, "success": ok, "msg": msg}
    except Exception as e:
        logger.error(f"{prefix} - 异常: {e}\n{traceback.format_exc()}")
        return {"user": user_disp, "success": False, "msg": str(e)}

def push_results(results, token, push_plus_hour, max_show):
    if push_plus_hour:
        try:
            if int(push_plus_hour) != datetime.now(pytz.timezone("Asia/Shanghai")).hour:
                logger.info("当前不是推送限制小时，跳过推送")
                return
        except:
            pass
    total = len(results)
    success = sum(1 for r in results if r["success"])
    summary = f"总账号数: {total}，成功: {success}，失败: {total - success} ({(success/total*100) if total else 0:.1f}%成功率)"
    logger.info("="*40 + "\n" + summary + "\n" + "="*40)

    if not token:
        return

    title = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 小米步数提交结果"
    content = f"<h3>{summary}</h3><ul>"
    for r in results[:max_show]:
        status = "✅成功" if r["success"] else "❌失败"
        content += f"<li>{status} - {r['user']} - {r['msg']}</li>"
    content += "</ul>"
    if len(results) > max_show:
        content += f"<p>仅展示前 {max_show} 个账号结果。</p>"
    push_plus(token, title, content)

def main():
    cfg = load_config_from_env()
    if not cfg:
        exit(1)

    # --- 兼容 ACCOUNTS 或老版 USER/PWD ---
    accounts = cfg.get("ACCOUNTS")
    if not accounts:
        user_raw = cfg.get("USER", "")
        pwd_raw = cfg.get("PWD", "")
        if not user_raw or not pwd_raw:
            logger.error("CONFIG 缺少 ACCOUNTS 或 USER/PWD 信息")
            exit(1)
        users = str(user_raw).split("#")
        pwds = str(pwd_raw).split("#")
        if len(users) != len(pwds):
            logger.error("USER 与 PWD 数量不一致")
            exit(1)
        accounts = [{"username": u, "password": p} for u, p in zip(users, pwds)]

    token = cfg.get("PUSH_PLUS_TOKEN", "")
    sleep_gap = float(cfg.get("SLEEP_GAP", 5))
    concurrent = str(cfg.get("USE_CONCURRENT", "false")).lower() == "true"
    push_plus_max = int(cfg.get("PUSH_PLUS_MAX", 30))
    push_plus_hour = cfg.get("PUSH_PLUS_HOUR", "")

    submitter = StepSubmitter()
    results = []

    if concurrent:
        max_workers = min(8, len(accounts))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(process_one
