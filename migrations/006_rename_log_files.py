"""
로그 파일명 변경: bot.log.YYYY-MM-DD → bot.YYYY-MM-DD.log
TimedRotatingFileHandler 형식 → DailyFileHandler 형식
"""

import os
import json
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

log_dir = os.path.join(BASE_DIR, conf["paths"]["logs_dir"])
if not os.path.isdir(log_dir):
    exit(0)

renamed = 0
for name in os.listdir(log_dir):
    # bot.log.2026-03-29 → bot.2026-03-29.log
    m = re.match(r'^(bot|server)\.log\.(\d{4}-\d{2}-\d{2})$', name)
    if m:
        prefix, date = m.groups()
        new_name = f"{prefix}.{date}.log"
        old_path = os.path.join(log_dir, name)
        new_path = os.path.join(log_dir, new_name)
        if not os.path.exists(new_path):
            os.rename(old_path, new_path)
            print(f"  {name} → {new_name}")
            renamed += 1

# 날짜 없는 bot.log, server.log가 있으면 오늘 날짜로
from datetime import datetime
today = datetime.now().strftime("%Y-%m-%d")
for prefix in ["bot", "server"]:
    old_path = os.path.join(log_dir, f"{prefix}.log")
    new_path = os.path.join(log_dir, f"{prefix}.{today}.log")
    if os.path.exists(old_path) and os.path.getsize(old_path) > 0:
        if os.path.exists(new_path):
            # 이미 있으면 합침
            with open(old_path, encoding="utf-8", errors="replace") as f:
                content = f.read()
            with open(new_path, "a", encoding="utf-8") as f:
                f.write(content)
            os.remove(old_path)
        else:
            os.rename(old_path, new_path)
        print(f"  {prefix}.log → {prefix}.{today}.log")
        renamed += 1

print(f"  {renamed}건 리네임 완료")
