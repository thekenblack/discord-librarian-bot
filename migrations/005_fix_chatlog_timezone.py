"""
chat.jsonl의 UTC 타임스탬프를 TZ 기준(기본 Asia/Seoul)으로 변환
"""

import os
import json
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

log_dir = os.path.join(BASE_DIR, conf["paths"]["logs_dir"])
chat_log = os.path.join(log_dir, "chat.jsonl")

if not os.path.exists(chat_log):
    print("  chat.jsonl 없음, 건너뜀")
    exit(0)

import zoneinfo
tz_name = os.getenv("TZ", "Asia/Seoul")
try:
    tz = zoneinfo.ZoneInfo(tz_name)
except Exception:
    print(f"  TZ '{tz_name}' 로드 실패, 건너뜀")
    exit(0)

lines = []
fixed = 0
with open(chat_log, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            ts = data.get("ts", "")
            if ts.endswith("+00:00") or ts.endswith("Z"):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                data["ts"] = dt.astimezone(tz).isoformat()
                fixed += 1
            lines.append(json.dumps(data, ensure_ascii=False))
        except Exception:
            lines.append(line)

with open(chat_log, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print(f"  {fixed}건 UTC → {tz_name} 변환 완료")
