"""
구 구조 잔여 파일/폴더 정리
- 빈 uploads/, backups/ 디렉토리 삭제
- 루트의 구 DB 파일 삭제 (이미 data/로 이동된 경우)
- ai_bot.log 삭제 (이미 logs/bot.log로 이동된 경우)
- 구 스크립트 잔여 삭제
"""

import os
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cleanup():
    # 빈 디렉토리 정리
    for d in ["uploads", "backups", "cogs", "ai"]:
        path = os.path.join(BASE_DIR, d)
        if os.path.isdir(path):
            try:
                if not os.listdir(path):
                    os.rmdir(path)
                    print(f"  빈 디렉토리 삭제: {d}/")
                else:
                    print(f"  건너뜀 (비어있지 않음): {d}/")
            except OSError:
                pass

    # 구 파일 정리 (data/로 이미 이동된 경우만)
    data_dir = os.path.join(BASE_DIR, "data")
    for db_name in ["library.db", "librarian.db"]:
        old = os.path.join(BASE_DIR, db_name)
        new = os.path.join(data_dir, db_name)
        if os.path.exists(old) and os.path.exists(new):
            os.remove(old)
            print(f"  구 DB 삭제: {db_name} (data/에 이미 존재)")

    # 구 로그 파일
    old_log = os.path.join(BASE_DIR, "ai_bot.log")
    new_log = os.path.join(BASE_DIR, "logs", "bot.log")
    if os.path.exists(old_log) and os.path.exists(new_log):
        os.remove(old_log)
        print(f"  구 로그 삭제: ai_bot.log (logs/bot.log에 이미 존재)")

    # 구 스크립트 잔여
    for f in ["bot.py", "ai.py", "library_db.py", "librarian_db.py",
              "utils.py", "migrate_db.py", "gc.sh", "gp.sh", "gs.sh", "gsync.sh"]:
        path = os.path.join(BASE_DIR, f)
        if os.path.exists(path):
            os.remove(path)
            print(f"  구 파일 삭제: {f}")


if __name__ == "__main__":
    print("[001] 구 구조 정리 중...")
    cleanup()
    print("[001] 완료")
