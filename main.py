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

# -------------------------- 基础配置与日志初始化 --------------------------
# 配置日志（统一格式，便于排查）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 目标步数提交接口（第三方接口）
TARGET_STEP_API = "https://wzz.wangzouzou.com/motion/api/motion/Xiaomi"

# 默认配置（可通过环境变量CONFIG覆盖）
DEFAULT_CONFIG = {
    "MIN_STEP": 6000,    # 每日最小步数
    "MAX_STEP": 24000,   # 每日最大步数（22点达到最大值）
    "SLEEP_GAP": 5,      # 多账号处理间隔（秒）
    "PUSH_PLUS_MAX": 30, # PushPlus单次最大推送账号数
    "USE_CONCURRENT": "False", # 是否启用多账号并发处理
    "PUSH_PLUS_HOUR": "", # 仅指定小时推送（如"20"表示仅20点推送）
    "PUSH_PLUS_TOKEN": "", # PushPlus推送Token（为空则不推送）
    "USER": "",          # 多账号用#分隔（如"user1@xxx.com#138xxxx1234"）
    "PWD": ""            # 多密码用#分隔（与账号一一对应）
}


# -------------------------- 工具函数（保留原脚本核心能力） --------------------------
def get_beijing_time():
    """获取北京时间（解决服务器UTC时区偏差）"""
    target_timezone = pytz.timezone('Asia/Shanghai')
    return datetime.now().astimezone(target_timezone)


def format_now():
    """格式化当前北京时间（用于日志和推送）"""
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")


def get_int_value_default(_config: dict, _key, default):
    """安全获取int类型配置，默认值兜底"""
    _config.setdefault(_key, default)
    try:
        return int(_config.get(_key))
    except (ValueError, TypeError):
        return default


def get_min_max_by_time(hour=None, minute=None):
    """按当前时间比例计算步数范围（0-22点线性递增，更符合真实运动规律）"""
    now = get_beijing_time()
    hour = hour if hour is not None else now.hour
    minute = minute if minute is not None else now.minute

    # 每日22点后步数不再增加（模拟夜间不运动）
    if hour >= 22:
        time_rate = 1.0
    elif hour < 0:
        time_rate = 0.0
    else:
        # 时间比例：(当前分钟数)/(22小时总分钟数)
        time_rate = min((hour * 60 + minute) / (22 * 60), 1.0)

    # 步数范围配置
    STEP_RANGES = {
        8: {"min": 6000, "max": 10000},
        12: {"min": 8000, "max": 14000},
        16: {"min": 10000, "max": 18000},
        20: {"min": 12000, "max": 22000},
        22: {"min": 15000, "max": 24000}
    }
    #"""根据当前时间获取对应的步数范围"""
    current_hour = hour
    logger.info(f"当前时间: {datetime.now()}, 小时: {current_hour}")
        
    # 找到最接近的配置时间段
    closest_hour = None
    steps = None
    max_step = 35000
    min_diff = float('inf')
        
    for hour in STEP_RANGES.keys():
        diff = abs(current_hour - hour)
        if diff < min_diff:
            min_diff = diff
            closest_hour = hour
        
    # 如果找到接近的配置且在合理范围内（2小时内），使用该配置
    if min_diff <= 2 and closest_hour in STEP_RANGES:
        step_config = STEP_RANGES[closest_hour]
        steps = random.randint(step_config['min'], step_config['max'])
        max_step = step_config['max']
        logger.info(f"使用 {closest_hour} 点配置，生成步数: {steps}")
    else:
        steps = 29889
        logger.info(f"使用默认步数: {steps}")
         
    min_step = steps
    # max_step = get_int_value_default(config, "MAX_STEP", 24000)

     # 计算当前时间对应的步数范围（避免低于最小步数）
    current_min = max(min_step, int(time_rate * min_step))
    current_max = max(max_step, int(time_rate * max_step))
    
    logger.info(f"当前时间[{hour:02d}:{minute:02d}]，步数范围：{current_min}~{current_max}")
    return current_min, current_max


def desensitize_user_name(user):
    """账号脱敏（日志/推送中隐藏中间字符，保护隐私）"""
    user_str = str(user).strip()
    if len(user_str) <= 8:
        ln = max(math.floor(len(user_str) / 3), 1)
        return f"{user_str[:ln]}***{user_str[-ln:]}"
    return f"{user_str[:3]}****{user_str[-4:]}"


def push_plus(title, content):
    """PushPlus微信推送（保留原脚本通知能力）"""
    if not PUSH_PLUS_TOKEN or PUSH_PLUS_TOKEN == "NO":
        logger.info("未配置PushPlus Token，跳过推送")
        return

    request_url = "http://www.pushplus.plus/send"
    data = {
        "token": PUSH_PLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "html",
        "channel": "wechat"
    }

    try:
        response = requests.post(request_url, data=data, timeout=15)
        response.raise_for_status()  # 抛出HTTP错误
        result = response.json()
        if result.get("code") == 200:
            logger.info(f"PushPlus推送成功：{result.get('msg')}")
        else:
            logger.error(f"PushPlus推送失败：{result.get('msg')}")
    except Exception as e:
        logger.error(f"PushPlus推送异常：{str(e)}", exc_info=True)


# -------------------------- 核心：步数提交类（替换为第三方接口） --------------------------
class StepSubmitter:
    def __init__(self):
        self.session = requests.Session()
        # 第三方接口请求头（必须匹配，否则接口拒绝）
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.7339.128 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://m.cqzz.top',
            'Referer': 'https://m.cqzz.top/',
            'X-Requested-With': 'XMLHttpRequest'
        }

    def validate_credentials(self, username, password):
        """账号密码格式验证（保留目标脚本的验证逻辑）"""
        # 手机号正则（13-9开头，11位）
        phone_pattern = r'^1[3-9]\d{9}$'
        # 邮箱正则（简化版，支持常见格式）
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

        # 基础校验
        if not username or not password:
            return False, "账号或密码不能为空"
        if ' ' in password:
            return False, "密码不能包含空格"
        # 格式校验（手机号或邮箱）
        if not (re.match(phone_pattern, username) or re.match(email_pattern, username)):
            return False, "账号格式错误（需为手机号或邮箱）"
        
        return True, "验证通过"

    def submit(self, username, password, steps):
        """提交步数到第三方接口（核心逻辑替换）"""
        # 1. 先验证账号密码格式
        is_valid, msg = self.validate_credentials(username, password)
        if not is_valid:
            return False, f"格式验证失败：{msg}"

        # 2. 构造接口请求参数（第三方接口要求的字段：phone/pwd/num）
        data = {
            "phone": username,  # 账号（手机号/邮箱）
            "pwd": password,    # 密码
            "num": steps        # 步数
        }

        logger.info(f"准备提交 - 账号：{desensitize_user_name(username)}，步数：{steps}")
        try:
            # 3. 发送POST请求到第三方接口
            response = self.session.post(
                url=TARGET_STEP_API,
                data=data,
                headers=self.headers,
                timeout=30  # 超时时间30秒，避免卡住
            )
            response.raise_for_status()  # 捕获HTTP 4xx/5xx错误

            # 4. 解析接口响应
            try:
                result = response.json()
            except json.JSONDecodeError:
                return False, f"接口响应格式错误：{response.text[:100]}"

            # 5. 处理接口返回结果（按第三方接口code判断）
            if result.get("code") == 200:
                return True, f"提交成功！步数：{steps}，接口反馈：{result.get('data', 'success')}"
            else:
                error_msg = result.get("data", "未知错误")
                # 特殊处理：提交频繁（接口常见限制）
                if "频繁" in str(error_msg):
                    return False, f"提交过于频繁，请间隔1小时后重试（{error_msg}）"
                return False, f"接口拒绝：{error_msg}"

        except requests.exceptions.RequestException as e:
            # 网络异常（超时、连接失败等）
            return False, f"网络请求错误：{str(e)}"
        except Exception as e:
            # 其他未知异常
            return False, f"提交逻辑异常：{str(e)}"


# -------------------------- 多账号执行逻辑（保留原脚本并发/间隔能力） --------------------------
def run_single_account(total: int, idx: int, username: str, password: str) -> dict:
    """单账号执行逻辑（封装为函数，支持并发）"""
    idx_info = f"[{idx+1}/{total}]" if total > 1 else ""
    user_desensitized = desensitize_user_name(username)
    log_prefix = f"{idx_info}账号：{user_desensitized}"
    result = {"user": user_desensitized, "success": False, "msg": "未执行"}

    try:
        # 1. 获取当前账号应提交的步数（按时间比例）
        min_step, max_step = get_min_max_by_time()
        steps = random.randint(min_step, max_step)  # 随机步数，更真实

        # 2. 初始化提交器并提交步数
        submitter = StepSubmitter()
        success, msg = submitter.submit(username, password, steps)

        # 3. 整理结果
        result["success"] = success
        result["msg"] = msg
        logger.info(f"{log_prefix} - {msg}")

    except Exception as e:
        # 捕获单账号执行异常
        err_msg = f"处理异常：{str(e)}\n{traceback.format_exc()[:200]}"
        result["msg"] = err_msg
        logger.error(f"{log_prefix} - {err_msg}", exc_info=True)

    return result


def push_to_push_plus(exec_results: list, summary: str):
    """结果推送（按原脚本逻辑，支持指定小时推送）"""
    # 检查是否需要推送（指定小时才推）
    if PUSH_PLUS_HOUR and PUSH_PLUS_HOUR.isdigit():
        current_hour = get_beijing_time().hour
        if current_hour != int(PUSH_PLUS_HOUR):
            logger.info(f"当前小时[{current_hour}]≠推送指定小时[{PUSH_PLUS_HOUR}]，跳过PushPlus")
            return

    # 构造推送内容（HTML格式，清晰展示）
    title = f"[{format_now()}] 小米步数提交结果"
    content = f"<h4>执行汇总</h4><p>{summary}</p>"

    # 限制推送账号数（避免内容过长）
    if len(exec_results) > PUSH_PLUS_MAX:
        content += f"<p>注：账号总数[{len(exec_results)}]超过推送上限[{PUSH_PLUS_MAX}]，仅展示前{PUSH_PLUS_MAX}个</p>"
        exec_results = exec_results[:PUSH_PLUS_MAX]

    # 构造账号结果列表
    content += "<h4>账号详情</h4><ul>"
    for res in exec_results:
        status = "✅ 成功" if res["success"] else "❌ 失败"
        content += f"<li>{status} - 账号：{res['user']} - 详情：{res['msg']}</li>"
    content += "</ul>"

    # 发送推送
    push_plus(title, content)


def execute():
    """主执行函数（解析配置+多账号处理）"""
    global config, PUSH_PLUS_TOKEN, PUSH_PLUS_HOUR, PUSH_PLUS_MAX

    # 1. 解析配置（从环境变量CONFIG读取，JSON格式）
    config_str = os.environ.get("CONFIG", "{}")
    try:
        config = json.loads(config_str)
        logger.info("CONFIG配置解析成功")
    except json.JSONDecodeError:
        logger.error("CONFIG格式错误！请严格按照JSON格式配置（双引号、无多余逗号）")
        logger.error(f"当前CONFIG：{config_str}")
        exit(1)

    # 2. 提取核心配置（带默认值兜底）
    # 账号密码（多账号用#分隔，必须一一对应）
    users = config.get("USER", "").split("#")
    passwords = config.get("PWD", "").split("#")
    # PushPlus配置
    PUSH_PLUS_TOKEN = config.get("PUSH_PLUS_TOKEN", "")
    PUSH_PLUS_HOUR = config.get("PUSH_PLUS_HOUR", "")
    PUSH_PLUS_MAX = get_int_value_default(config, "PUSH_PLUS_MAX", 30)
    # 多账号处理配置
    sleep_seconds = float(config.get("SLEEP_GAP", 5))  # 账号间隔（秒）
    use_concurrent = config.get("USE_CONCURRENT", "False").lower() == "true"  # 是否并发

    # 3. 基础校验（账号密码数量匹配）
    if len(users) != len(passwords):
        logger.error(f"账号数[{len(users)}]与密码数[{len(passwords)}]不匹配！请检查CONFIG")
        exit(1)
    total_accounts = len(users)
    if total_accounts == 0:
        logger.error("未配置任何账号！请在CONFIG中设置USER和PWD")
        exit(1)
    logger.info(f"共加载{total_accounts}个账号，并发模式：{'开启' if use_concurrent else '关闭'}，间隔：{sleep_seconds}秒")

    # 4. 多账号执行（支持并发/间隔）
    exec_results = []
    if use_concurrent:
        # 并发执行（使用线程池，效率高）
        logger.info("开启并发处理，最大线程数：5（默认）")
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            # 构造任务列表
            tasks = [
                executor.submit(run_single_account, total_accounts, idx, user, pwd)
                for idx, (user, pwd) in enumerate(zip(users, passwords))
            ]
            # 收集结果
            for task in concurrent.futures.as_completed(tasks):
                exec_results.append(task.result())
    else:
        # 串行执行（带间隔，避免触发接口频率限制）
        for idx, (user, pwd) in enumerate(zip(users, passwords)):
            exec_results.append(run_single_account(total_accounts, idx, user, pwd))
            # 非最后一个账号，添加间隔
            if idx < total_accounts - 1:
                logger.info(f"间隔{sleep_seconds}秒后处理下一个账号...")
                time.sleep(sleep_seconds)

    # 5. 统计结果并推送
    success_count = sum(1 for res in exec_results if res["success"])
    fail_count = total_accounts - success_count
    summary = f"总账号数：{total_accounts}，成功：{success_count}，失败：{fail_count}（{success_count/total_accounts*100:.1f}%成功率）"
    logger.info(f"\n{'='*50}\n{summary}\n{'='*50}")

    # 推送结果（满足条件时）
    push_to_push_plus(exec_results, summary)


# -------------------------- 脚本入口（统一启动逻辑） --------------------------
if __name__ == "__main__":
    # 全局变量（配置相关）
    config = {}
    PUSH_PLUS_TOKEN = ""
    PUSH_PLUS_HOUR = ""
    PUSH_PLUS_MAX = 30

    try:
        # 检查环境变量是否存在（核心配置来源）
        if "CONFIG" not in os.environ:
            raise ValueError("未检测到CONFIG环境变量！请先配置账号、密码等核心参数")
        
        # 启动主执行逻辑
        execute()
        exit(0)

    except Exception as e:
        # 捕获脚本启动级异常
        logger.error(f"脚本启动失败：{str(e)}", exc_info=True)
        # 若配置了PushPlus，推送启动失败通知
        if PUSH_PLUS_TOKEN and PUSH_PLUS_TOKEN != "NO":
            push_plus(
                title=f"[{format_now()}] 小米步数脚本启动失败",
                content=f"<p>错误原因：{str(e)}</p><p>请检查CONFIG配置或日志详情</p>"
            )
        exit(1)
