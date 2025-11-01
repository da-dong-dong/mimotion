#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版步数提交脚本 v2
 - 更详细地记录响应（status/text/json）
 - 支持 TARGET_STEP_API 环境变量覆盖
 - 支持可选 VERIFY_API 用于提交后校验当前步数（如果你有此接口）
 - 支持重试与 dry-run
 - 将每次接口原始响应写入文件便于离线分析
"""
import os
import json
import time
import random
import logging
import traceback
import requests
from datetime import datetime
import pytz
import re

# ---------- 日志 ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("step_v2")

# ---------- 默认配置 ----------
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

# 默认 API（可被环境变量覆盖）
DEFAULT_TARGET_API = "https://wzz.wangzouzou.com/motion/api/motion/Xiaomi"

# ---------- 工具 ----------
def now_beijing_str():
    return datetime.now(pytz.timezone("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

def desensitize(user):
    u = str(user)
    return f"{u[:3]}****{u[-4:]}" if len(u) > 8 else f"{u[:1]}***{u[-1:]}"

def save_response_for_debug(username, resp_text, resp_status, resp_json=None):
    fn = f"last_response_{re.sub(r'[^0-9a-zA-Z]', '_', username)}.json"
    try:
        with open(fn, "w", encoding="utf-8") as f:
            out = {"timestamp": now_beijing_str(), "status": resp_status, "text": resp_text}
            if resp_json is not None:
                out["json"] = resp_json
            json.dump(out, f, ensure_ascii=False, indent=2)
        logger.info(f"原始响应已写入 {fn}")
    except Exception as e:
        logger.warning(f"写响应文件失败: {e}")

# ---------- 步数生成 ----------
def calc_step_range():
    now = datetime.now(pytz.timezone("Asia/Shanghai"))
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

# ---------- 提交逻辑 ----------
class StepSubmitter:
    def __init__(self, target_api, dry_run=False, verbose=False, headers=None, timeout=30):
        self.s = requests.Session()
        self.target = target_api
        self.dry_run = dry_run
        self.verbose = verbose
        self.timeout = timeout
        self.headers = headers or {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://m.cqzz.top',
            'Referer': 'https://m.cqzz.top/',
            'X-Requested-With': 'XMLHttpRequest'
        }

    def validate(self, username, password):
        if not username or not password:
            return False, "账号或密码为空"
        if re.match(r"^1[3-9]\d{9}$", username) or re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", username):
            return True, ""
        return False, "账号格式错误"

    def submit_once(self, username, password, steps):
        valid, msg = self.validate(username, password)
        if not valid:
            return False, msg, None
        payload = {"phone": username, "pwd": password, "num": steps}
        if self.dry_run:
            logger.info(f"[DRY_RUN] 将 POST 到 {self.target}，payload={payload}")
            return True, "dry_run - 未实际提交", {"dry_run": True, "payload": payload}
        try:
            resp = self.s.post(self.target, data=payload, headers=self.headers, timeout=self.timeout)
            text = resp.text
            status = resp.status_code
            json_body = None
            try:
                json_body = resp.json()
            except Exception:
                json_body = None
            # 保存原始响应
            save_response_for_debug(username, text, status, json_body)
            if self.verbose:
                logger.info(f"HTTP {status} - text: {text}")
            return True, "请求已发送", {"status": status, "text": text, "json": json_body}
        except Exception as e:
            return False, f"请求异常：{e}", None

    def submit_with_retry(self, username, password, steps, retries=3, backoff=2):
        attempt = 0
        last_info = None
        while attempt <= retries:
            ok, msg, info = self.submit_once(username, password, steps)
            attempt += 1
            if not ok:
                logger.warning(f"提交尝试 {attempt}/{retries+1} 失败: {msg}")
                last_info = (ok, msg, info)
                time.sleep(backoff ** attempt)
                continue
            # 当请求成功发出（HTTP 层）后，进一步判断返回 JSON 是否确实表示生效
            info_json = info.get("json") if info else None
            status = info.get("status") if info else None
            text = info.get("text") if info else None

            suspicious = False
            if status != 200:
                suspicious = True
                reason = f"HTTP状态码 {status}"
            else:
                # 常见正确响应： {"code":200, ...} 或者包含 success:true，message:'success'
                if info_json:
                    if (info_json.get("code") == 200) or (info_json.get("success") is True) or ("success" in str(info_json).lower()):
                        # 看起来是成功的，但仍需核验（如果提供 VERIFY_API）
                        logger.info("接口返回表面成功，进一步需要校验（如果你启用了 VERIFY_API）")
                        return True, "接口返回200/成功字段，需要验证是否同步到目标平台", info
                    else:
                        suspicious = True
                        reason = f"返回 JSON 未表现为明确成功: {info_json}"
                else:
                    suspicious = True
                    reason = f"返回非 JSON: {text[:200] if text else ''}"

            if suspicious:
                logger.warning(f"提交疑似失败或不可信（尝试 {attempt}/{retries+1}）: {reason}")
                last_info = (ok, f"疑似失败: {reason}", info)
                if attempt <= retries:
                    logger.info(f"等待 {backoff ** attempt} 秒后重试...")
                    time.sleep(backoff ** attempt)
                    continue
                else:
                    return False, f"多次尝试仍疑似失败: {reason}", info
        return last_info if last_info else (False, "未知错误", None)

# ---------- 验证 helper（可选） ----------
def verify_via_api(verify_api, username):
    """
    可选：如果你掌握一个查询当前步数的接口（VERIFY_API），在提交后可以调用此函数确认是否真的更新。
    verify_api 示例: https://your.service/api/get_steps?phone={phone}
    """
    if not verify_api:
        return None
    try:
        url = verify_api.format(phone=username)
        r = requests.get(url, timeout=10)
        try:
            j = r.json()
        except Exception:
            j = {"status_code": r.status_code, "text": r.text[:500]}
        return True, j
    except Exception as e:
        return False, f"verify 请求异常: {e}"

# ---------- 主流程 ----------
def run_for_account(idx, total, username, password, cfg):
    user_d = desensitize(username)
    log_prefix = f"[{idx+1}/{total}] {user_d}"
    try:
        steps = calc_step_range()
        target_api = os.environ.get("TARGET_STEP_API", DEFAULT_TARGET_API)
        dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
        verbose = os.environ.get("VERBOSE", "false").lower() == "true"
        submitter = StepSubmitter(target_api, dry_run=dry_run, verbose=verbose)
        ok, msg, info = submitter.submit_with_retry(username, password, steps, retries=3, backoff=2)
        if ok:
            logger.info(f"{log_prefix} - ✅ {msg}")
        else:
            logger.error(f"{log_prefix} - ❌ {msg}")
        # 如果配置了 VERIFY_API，尝试去查询当前步数确认
        verify_api = os.environ.get("VERIFY_API", "")
        if verify_api:
            v_ok, v_res = verify_via_api(verify_api, username)
            if v_ok:
                logger.info(f"{log_prefix} - VERIFY API 返回: {v_res}")
            else:
                logger.warning(f"{log_prefix} - VERIFY 请求失败: {v_res}")
        return {"user": user_d, "success": ok, "msg": msg, "info": info}
    except Exception as e:
        logger.error(f"{log_prefix} - 异常：{e}\n{traceback.format_exc()[:400]}")
        return {"user": user_d, "success": False, "msg": str(e)}

def push_results_stub(results, token, hour_limit, max_count):
    # 这里保留原 push 逻辑的占位（你可以把原 push_plus 函数接回）
    success = sum(1 for r in results if r["success"])
    total = len(results)
    logger.info("="*40)
    logger.info(f"总账号数: {total}, 成功: {success}, 失败: {total-success}")
    logger.info("="*40)

def main():
    cfg_str = os.environ.get("CONFIG", "{}")
    try:
        cfg = json.loads(cfg_str)
    except Exception:
        logger.error("CONFIG 不是合法 JSON")
        return
    config = {**DEFAULT_CONFIG, **cfg}
    users = config.get("USER", "").split("#")
    pwds = config.get("PWD", "").split("#")
    if not users or not users[0] or len(users) != len(pwds):
        logger.error("账号或密码数量不匹配/为空，请检查 CONFIG")
        return
    logger.info(f"加载 {len(users)} 个账号，模式：{'并发(未实现)' if config.get('USE_CONCURRENT','False').lower()=='true' else '串行'}")
    results = []
    for i, (u, p) in enumerate(zip(users, pwds)):
        res = run_for_account(i, len(users), u, p, config)
        results.append(res)
        # sleep
        if i < len(users)-1:
            try:
                gap = float(config.get("SLEEP_GAP", 5))
            except Exception:
                gap = 5
            time.sleep(gap)
    # push summary
    push_results_stub(results, config.get("PUSH_PLUS_TOKEN",""), config.get("PUSH_PLUS_HOUR",""), int(config.get("PUSH_PLUS_MAX",30)))

if __name__ == "__main__":
    if "CONFIG" not in os.environ:
        logger.error("未检测到 CONFIG 环境变量，请设置后重试（示例见下方）")
    else:
        main()
