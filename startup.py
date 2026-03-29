"""
봇 프로세스 관리 스타터
bot.py (라이브러리 봇) + ai.py (AI 사서봇) 동시 관리
종료코드 42 수신 시 재시작, 그 외엔 종료
"""

import subprocess
import sys
import os
import signal

RESTART_CODE = 42
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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
