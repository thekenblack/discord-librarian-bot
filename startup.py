"""
봇 프로세스 관리 스타터
config.json에서 봇 목록을 읽어 동시 관리
종료코드 42 수신 시 git pull + 전체 재시작
"""

import subprocess
import sys
import os
import json
import shutil
from datetime import datetime

RESTART_CODE = 42
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── venv 자동 설치 + 감지 (리눅스) ─────────────────
if sys.platform != "win32":
    VENV_DIR = os.path.join(BASE_DIR, "venv")
    VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")

    if not os.path.exists(VENV_PYTHON):
        print("[설치] venv 생성 중...")
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)

    if os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
        os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)

    # 매 시작 시 패키지 업데이트
    print("[설치] 패키지 업데이트 중...")
    subprocess.run(
        [VENV_PYTHON, "-m", "pip", "install", "--progress-bar", "on", "-r",
         os.path.join(BASE_DIR, "requirements.txt")],
        cwd=BASE_DIR,
    )

# ── .env 체크 ───────────────────────────────────────
if not os.path.exists(os.path.join(BASE_DIR, ".env")):
    print("[오류] .env 파일이 없습니다.")
    print("  → cp env.example .env 후 토큰과 API 키를 입력하세요.")
    sys.exit(1)

# ── config.json 로드 ────────────────────────────────
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
if not os.path.exists(CONFIG_PATH):
    print("[오류] config.json 파일이 없습니다.")
    sys.exit(1)

with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = json.load(f)

_paths = CONFIG.get("paths", {})
DATA_DIR    = os.path.join(BASE_DIR, _paths.get("data_dir", "data"))
FILES_DIR   = os.path.join(BASE_DIR, _paths.get("files_dir", "files"))
LOG_DIR     = os.path.join(BASE_DIR, _paths.get("logs_dir", "logs"))
BACKUP_DIR  = os.path.join(BASE_DIR, _paths.get("backups_dir", "data/backups"))
MIGRATIONS_DIR = os.path.join(BASE_DIR, _paths.get("migrations_dir", "migrations"))

# ── 런타임 디렉토리 생성 ────────────────────────────
for d in [DATA_DIR, FILES_DIR, LOG_DIR, BACKUP_DIR, MIGRATIONS_DIR]:
    os.makedirs(d, exist_ok=True)

# ── 파일 마이그레이션 (구 → 신 구조) ────────────────
def _migrate_file(old_path, new_path):
    """old가 있고 new가 없으면 이동"""
    if os.path.exists(old_path) and not os.path.exists(new_path):
        shutil.move(old_path, new_path)
        print(f"[마이그레이션] {old_path} → {new_path}")

def _migrate_dir_contents(old_dir, new_dir):
    """old_dir 안의 파일들을 new_dir로 이동"""
    if not os.path.isdir(old_dir):
        return
    for name in os.listdir(old_dir):
        old_path = os.path.join(old_dir, name)
        new_path = os.path.join(new_dir, name)
        if os.path.isfile(old_path) and not os.path.exists(new_path):
            shutil.move(old_path, new_path)
            print(f"[마이그레이션] {old_path} → {new_path}")

# DB 파일: 루트 → data/
_db = CONFIG.get("db", {})
_migrate_file(
    os.path.join(BASE_DIR, _db.get("library", "library.db")),
    os.path.join(DATA_DIR, _db.get("library", "library.db")),
)
_migrate_file(
    os.path.join(BASE_DIR, _db.get("librarian", "librarian.db")),
    os.path.join(DATA_DIR, _db.get("librarian", "librarian.db")),
)

# 업로드 파일: uploads/ → files/
_migrate_dir_contents(os.path.join(BASE_DIR, "uploads"), FILES_DIR)

# 백업: backups/ → data/backups/
_migrate_dir_contents(os.path.join(BASE_DIR, "backups"), BACKUP_DIR)

# 로그: ai_bot.log → logs/bot.log
_migrate_file(
    os.path.join(BASE_DIR, "ai_bot.log"),
    os.path.join(LOG_DIR, "bot.log"),
)

# 구 단일 DB 마이그레이션
OLD_DB = os.path.join(BASE_DIR, "librarian_bot.db")
if os.path.exists(OLD_DB) and not os.path.exists(os.path.join(DATA_DIR, "library.db")):
    print("[마이그레이션] librarian_bot.db 분리 중...")
    migrate_script = os.path.join(BASE_DIR, "migrate_db.py")
    if os.path.exists(migrate_script):
        subprocess.run([sys.executable, migrate_script], cwd=BASE_DIR)

# ── DB 마이그레이션 스크립트 실행 ────────────────────
if os.path.isdir(MIGRATIONS_DIR):
    tracking_file = os.path.join(DATA_DIR, "migrations_applied.json")
    applied = set()
    if os.path.exists(tracking_file):
        with open(tracking_file, encoding="utf-8") as f:
            applied = set(json.load(f))

    # 하위 폴더(날짜_커밋) 포함 재귀 탐색, 상대경로 기준 정렬
    scripts = []
    for root, dirs, files in os.walk(MIGRATIONS_DIR):
        for f in files:
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(root, f), MIGRATIONS_DIR)
                scripts.append((rel, os.path.join(root, f)))
    scripts.sort(key=lambda x: x[0])

    for script_name, script_path in scripts:
        if script_name in applied:
            continue
        print(f"[마이그레이션] {script_name} 실행 중...")
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=BASE_DIR,
        )
        if result.returncode == 0:
            applied.add(script_name)
            with open(tracking_file, "w", encoding="utf-8") as f:
                json.dump(sorted(applied), f)
            print(f"[마이그레이션] {script_name} 완료")
        else:
            print(f"[마이그레이션] {script_name} 실패 (코드: {result.returncode})")

# ── DB 패치 (로컬 전용, gitignore) ─────────────────
PATCHES_DIR = os.path.join(BASE_DIR, "patches")

patches_tracking = os.path.join(DATA_DIR, "patches_applied.json")
patches_applied = set()
if os.path.exists(patches_tracking):
    with open(patches_tracking, encoding="utf-8") as f:
        patches_applied = set(json.load(f))

for patch_file in sorted(os.listdir(PATCHES_DIR)) if os.path.isdir(PATCHES_DIR) else []:
    if not (patch_file.endswith(".sql") or patch_file.endswith(".py")):
        continue
    if patch_file in patches_applied:
        continue

    patch_path = os.path.join(PATCHES_DIR, patch_file)
    print(f"[패치] {patch_file} 실행 중...")

    if patch_file.endswith(".sql"):
        import sqlite3
        # 파일명에서 대상 DB 추론: library_ → library.db, librarian_ → librarian.db
        if patch_file.startswith("library_"):
            target_db = os.path.join(DATA_DIR, _db.get("library", "library.db"))
        else:
            target_db = os.path.join(DATA_DIR, _db.get("librarian", "librarian.db"))
        try:
            with open(patch_path, encoding="utf-8") as f:
                sql = f.read()
            conn = sqlite3.connect(target_db)
            conn.executescript(sql)
            conn.close()
            patches_applied.add(patch_file)
            print(f"[패치] {patch_file} 완료 → {os.path.basename(target_db)}")
        except Exception as e:
            print(f"[패치] {patch_file} 실패: {e}")

    elif patch_file.endswith(".py"):
        result = subprocess.run(
            [sys.executable, patch_path], cwd=BASE_DIR,
        )
        if result.returncode == 0:
            patches_applied.add(patch_file)
            print(f"[패치] {patch_file} 완료")
        else:
            print(f"[패치] {patch_file} 실패 (코드: {result.returncode})")

if os.path.isdir(PATCHES_DIR):
    with open(patches_tracking, "w", encoding="utf-8") as f:
        json.dump(sorted(patches_applied), f)

# ── DB 백업 ─────────────────────────────────────────
for db_name in [_db.get("library", "library.db"), _db.get("librarian", "librarian.db")]:
    db_path = os.path.join(DATA_DIR, db_name)
    if os.path.exists(db_path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"{db_name}.{timestamp}")
        shutil.copy2(db_path, backup_path)
        print(f"[백업] {db_name} → {backup_path}")

        # 오래된 백업 정리 (각 DB당 최근 5개만 유지)
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith(db_name + ".")])
        for old in backups[:-5]:
            os.remove(os.path.join(BACKUP_DIR, old))

# ── 봇 목록 로드 ────────────────────────────────────
BOTS = []
for bot_conf in CONFIG.get("bots", []):
    BOTS.append({
        "name": bot_conf["name"],
        "module": bot_conf["module"],
    })

if not BOTS:
    print("[오류] config.json에 봇이 정의되지 않았습니다.")
    sys.exit(1)


def main():
    import time

    processes: dict[str, subprocess.Popen] = {}
    crash_counts: dict[str, int] = {}
    MAX_CRASHES = 3

    def start_bot(bot):
        print(f"[Starter] {bot['name']} 시작... (python -m {bot['module']})")
        proc = subprocess.Popen(
            [sys.executable, "-m", bot["module"]],
            cwd=BASE_DIR,
        )
        processes[bot["name"]] = proc
        return proc

    def stop_all():
        for name, proc in processes.items():
            if proc.poll() is None:
                print(f"[Starter] {name} 종료 중...")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    for bot in BOTS:
        start_bot(bot)

    try:
        while processes:
            for bot in BOTS:
                name = bot["name"]
                proc = processes.get(name)
                if proc is None:
                    continue

                ret = proc.poll()
                if ret is None:
                    continue

                if ret == RESTART_CODE:
                    print(f"[Starter] {name} 재시작 신호 수신, git pull 실행 중...")
                    pull = subprocess.run(
                        ["git", "pull"],
                        capture_output=True, text=True, cwd=BASE_DIR
                    )
                    pull_output = (pull.stdout + pull.stderr).strip()
                    if pull.returncode != 0:
                        print(f"[Starter] git pull 실패:\n{pull_output}")
                    else:
                        print(f"[Starter] git pull 완료:\n{pull_output}")

                    # pip install (패키지 변경 대응)
                    print("[Starter] 패키지 업데이트 중...")
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-q", "-r",
                         os.path.join(BASE_DIR, "requirements.txt")],
                        cwd=BASE_DIR,
                    )

                    print("[Starter] 전체 재시작합니다...")
                    stop_all()
                    processes.clear()
                    crash_counts.clear()
                    for b in BOTS:
                        start_bot(b)
                    break
                else:
                    print(f"[Starter] {name} 비정상 종료 (코드: {ret})")
                    del processes[name]
                    crash_counts[name] = crash_counts.get(name, 0) + 1
                    if crash_counts[name] >= MAX_CRASHES:
                        print(f"[Starter] {name} {MAX_CRASHES}회 연속 크래시, 포기합니다.")
                    else:
                        print(f"[Starter] {name} 재시도 ({crash_counts[name]}/{MAX_CRASHES})...")
                        time.sleep(3)
                        start_bot(bot)

            else:
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n[Starter] 종료합니다.")
        stop_all()


if __name__ == "__main__":
    main()
