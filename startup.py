"""
봇 프로세스 관리 스타터
bot.py (라이브러리 봇) + ai.py (AI 사서봇) 동시 관리
종료코드 42 수신 시 재시작, 그 외엔 종료
"""

import subprocess
import sys
import os
import signal

# venv 자동 설치 + 감지
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, "venv")
if sys.platform == "win32":
    VENV_PYTHON = os.path.join(VENV_DIR, "Scripts", "python.exe")
else:
    VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")

if not os.path.exists(VENV_PYTHON):
    print("[설치] venv 생성 중...")
    subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
    print("[설치] 패키지 설치 중...")
    subprocess.run([VENV_PYTHON, "-m", "pip", "install", "-r",
                    os.path.join(BASE_DIR, "requirements.txt")], check=True)
    print("[설치] 완료!")

if sys.executable != VENV_PYTHON:
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)

RESTART_CODE = 42
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# .env 체크
if not os.path.exists(os.path.join(BASE_DIR, ".env")):
    print("[오류] .env 파일이 없습니다.")
    print("  → cp env.example .env 후 토큰과 API 키를 입력하세요.")
    sys.exit(1)

# 마이그레이션 (기존 단일 DB → 분리)
OLD_DB = os.path.join(BASE_DIR, "librarian_bot.db")
if os.path.exists(OLD_DB) and not os.path.exists(os.path.join(BASE_DIR, "library.db")):
    print("[마이그레이션] librarian_bot.db 분리 중...")
    import subprocess
    subprocess.run([sys.executable, os.path.join(BASE_DIR, "migrate_db.py")], cwd=BASE_DIR)

# DB 백업
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

import shutil
from datetime import datetime
for db_file in ["library.db", "librarian.db"]:
    db_path = os.path.join(BASE_DIR, db_file)
    if os.path.exists(db_path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"{db_file}.{timestamp}")
        shutil.copy2(db_path, backup_path)
        print(f"[백업] {db_file} → {backup_path}")

        # 오래된 백업 정리 (각 DB당 최근 5개만 유지)
        backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith(db_file + ".")])
        for old in backups[:-5]:
            os.remove(os.path.join(BACKUP_DIR, old))

BOTS = [
    {"name": "라이브러리 봇", "script": os.path.join(BASE_DIR, "bot.py")},
    {"name": "AI 사서봇",      "script": os.path.join(BASE_DIR, "ai.py")},
]


def main():
    processes: dict[str, subprocess.Popen] = {}

    def start_bot(bot):
        print(f"[Starter] {bot['name']} 시작...")
        proc = subprocess.Popen([sys.executable, bot["script"]])
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

    # 전체 시작
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

                # 프로세스 종료됨
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

                    # 전체 재시작
                    print("[Starter] 전체 재시작합니다...")
                    stop_all()
                    processes.clear()
                    for b in BOTS:
                        start_bot(b)
                    break
                else:
                    print(f"[Starter] {name} 종료 (코드: {ret})")
                    del processes[name]

            else:
                # 아무것도 종료되지 않았으면 잠깐 대기
                import time
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n[Starter] 종료합니다.")
        stop_all()


if __name__ == "__main__":
    main()
