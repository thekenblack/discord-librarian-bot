"""
기존 bot.log를 날짜별로 분리 (TimedRotatingFileHandler 호환)
"""

import os
import json
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

log_dir = os.path.join(BASE_DIR, conf["paths"]["logs_dir"])
old_log = os.path.join(log_dir, "bot.log")

if not os.path.exists(old_log):
    print("  bot.log 없음, 건너뜀")
    exit(0)

if os.path.getsize(old_log) < 100000:
    print("  bot.log 100KB 미만, 건너뜀")
    exit(0)

print("  기존 bot.log를 날짜별로 분리 중...")

dates_seen = set()
with open(old_log, encoding="utf-8", errors="replace") as f:
    for line in f:
        if len(line) >= 10 and line[4] == '-' and line[7] == '-':
            dates_seen.add(line[:10])

today_str = datetime.now().strftime("%Y-%m-%d")

for date in sorted(dates_seen):
    if date == today_str:
        continue
    date_log = old_log + "." + date
    if os.path.exists(date_log):
        continue
    with open(old_log, encoding="utf-8", errors="replace") as f_in, \
         open(date_log, "w", encoding="utf-8") as f_out:
        for line in f_in:
            if line.startswith(date):
                f_out.write(line)
    print(f"  → {os.path.basename(date_log)}")

# 오늘 로그만 남기기
today_lines = []
with open(old_log, encoding="utf-8", errors="replace") as f:
    for line in f:
        if line.startswith(today_str):
            today_lines.append(line)

with open(old_log, "w", encoding="utf-8") as f:
    f.writelines(today_lines)

print(f"  bot.log에 오늘({today_str}) 로그만 유지")
