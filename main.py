#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import random
import time
import json
import logging
import traceback
import re
import math
from datetime import datetime
import pytz
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

# ======================= 日志配置 =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ======================= 全局常量 =======================
TARGET_STEP_API = "https://wzz.wangzouzou.com/motion/api/motion/Xiaomi"

DEFAULT_CONFIG = {
    "MIN_STEP": 6000,
    "MAX_STEP": 24000,
    "SLEEP_GAP": 5,
    "PUSH_PLUS_MAX": 30,
    "USE_CONCURRENT": "False",
    "PUSH_PLUS_HOUR": "",
    "PUSH_PLUS_TOKEN": "",
    "USER": "",
    "PWD": ""
}

# ======================= 工具函数 =======================
def get_beijing_time():
    """获取北京时间"""
    return datetime.now(pytz.timezone("Asia/Shanghai"))

def format_now():
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")

def get_int(cfg, key, default):
    """安全获取整型"""
    try:
        return int(cfg.get(key, default))
    except (ValueError, TypeError):
        return default

def desensitize(user):
    """账号脱敏"""
    user = str(user).strip()
    return f"{user[:3]}****{user[-4:]}" if len(user) > 8 else f"{user[:1]}***{user[-1:]}"

def push_plus(token, title, content):
    """PushPlus 推送"""
    if not token:
        logger.info("未配置PushPlus Token，跳过推送")
        return
    url = "http://www.pushplus.plus/send"
    data = {"token": token, "title": title, "content": content, "template": "html"}
    try:
        resp = requests.post(url, data=data, timeout=10)
        if resp.ok and resp.json().get("code") == 200:
            logger.info("✅ PushPlus 推送成功")
        else:
            logger.warning(f"⚠️ PushPlus 推送失败：{resp.text}")
    except Exception as e:
        logger.error(f"PushPlus 推送异常：{e}")

# ======================= 步数逻辑 =======================
def calc_step_range():
    """根据时间计算步数范围"""
    now = get_beijing_time()
    h = now.hour
    ranges = {
        8: (29988, 29999),
        12: (29988, 29999),
        14: (29988, 29999),
        16: (29988, 29999),
        22: (29988, 29999)
    }
    # 找最近时间段
    closest_hour = min(ranges, key=lambda x: abs(h - x))
    base_min, base_max = ranges[closest_hour]
    steps = random.randint(base_min, base_max)
    logger.info(f"时间[{h}点] 使用配置 {closest_hour} 点范围：{base_min}-{base_max}，生成步数：{steps}")
    return steps

# ======================= 提交类 =======================
class StepSubmitter:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': 'https://m.cqzz.top',
        'Referer': 'https://m.cqzz.top/',
        'X-Requested-With': 'XMLHttpRequest'
    }

    def __init__(self):
        self.s = requests.Session()

    def validate(self, username, password):
        if not username or not password:
            return False, "账号或密码为空"
        if re.match(r"^1[3-9]\d{9}$", username) or re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", username):
            return True, ""
        return False, "账号格式错误"

    def submit(self, username, password, steps):
        valid, msg = self.validate(username, password)
        if not valid:
            return False, msg
        try:
            resp = self.s.post(
                TARGET_STEP_API,
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

# ======================= 主逻辑 =======================
def run_account(idx, total, username, password):
    user_d = desensitize(username)
    log_prefix = f"[{idx+1}/{total}] {user_d}"
    try:
        steps = calc_step_range()
        ok, msg = StepSubmitter().submit(username, password, steps)
        result = {"user": user_d, "success": ok, "msg": msg}
        logger.info(f"{log_prefix} - {'✅成功' if ok else '❌失败'} - {msg}")
        return result
    except Exception as e:
        err = f"异常：{e}\n{traceback.format_exc()[:150]}"
        logger.error(f"{log_prefix} - {err}")
        return {"user": user_d, "success": False, "msg": err}

def push_results(results, token, hour_limit, max_count):
    """推送汇总结果"""
    if hour_limit and get_beijing_time().hour != int(hour_limit):
        logger.info(f"非推送指定小时({hour_limit})，跳过推送")
        return

    success = sum(1 for r in results if r["success"])
    total = len(results)
    summary = f"总账号数: {total}，成功: {success}，失败: {total - success} ({success/total*100:.1f}%成功率)"
    logger.info("="*50 + f"\n{summary}\n" + "="*50)

    if not token:
        return

    title = f"[{format_now()}] 小米步数提交结果"
    content = f"<h3>{summary}</h3><ul>"
    for r in results[:max_count]:
        status = "✅成功" if r["success"] else "❌失败"
        content += f"<li>{status} - {r['user']} - {r['msg']}</li>"
    content += "</ul>"
    if len(results) > max_count:
        content += f"<p>仅展示前 {max_count} 个账号结果。</p>"
    push_plus(token, title, content)

# ======================= 启动入口 =======================
def main():
    cfg_str = os.environ.get("CONFIG", "{}")
    try:
        cfg = json.loads(cfg_str)
    except json.JSONDecodeError:
        logger.error(f"CONFIG格式错误：{cfg_str}")
        return

    # 合并默认配置
    config = {**DEFAULT_CONFIG, **cfg}
    users = config["USER"].split("#")
    pwds = config["PWD"].split("#")

    if len(users) != len(pwds) or not users[0]:
        logger.error("账号与密码数量不匹配或为空")
        return

    token = config.get("PUSH_PLUS_TOKEN", "")
    hour_limit = config.get("PUSH_PLUS_HOUR", "")
    sleep_gap = float(config.get("SLEEP_GAP", 5))
    concurrent = config.get("USE_CONCURRENT", "false").lower() == "true"
    max_push = get_int(config, "PUSH_PLUS_MAX", 30)

    logger.info(f"加载 {len(users)} 个账号，模式：{'并发' if concurrent else '串行'}")

    results = []
    if concurrent:
        with ThreadPoolExecutor(max_workers=min(5, len(users))) as ex:
            tasks = [ex.submit(run_account, i, len(users), u, p) for i, (u, p) in enumerate(zip(users, pwds))]
            results = [t.result() for t in as_completed(tasks)]
    else:
        for i, (u, p) in enumerate(zip(users, pwds)):
            results.append(run_account(i, len(users), u, p))
            if i < len(users) - 1:
                time.sleep(sleep_gap)

    push_results(results, token, hour_limit, max_push)


if __name__ == "__main__":
    try:
        if "CONFIG" not in os.environ:
            raise ValueError("未检测到CONFIG环境变量，请设置账号信息")
        main()
    except Exception as e:
        logger.error(f"启动失败：{e}", exc_info=True)
