"""
기존 단일 DB(librarian_bot.db)를 library.db + librarian.db로 분리 마이그레이션
"""
import sqlite3
import os
import shutil

OLD_DB = "librarian_bot.db"
LIBRARY_DB = "library.db"
LIBRARIAN_DB = "librarian.db"

if not os.path.exists(OLD_DB):
    print(f"{OLD_DB} 없음. 마이그레이션 불필요.")
    exit(0)

if os.path.exists(LIBRARY_DB) or os.path.exists(LIBRARIAN_DB):
    print(f"{LIBRARY_DB} 또는 {LIBRARIAN_DB}가 이미 존재합니다. 마이그레이션 건너뜀.")
    exit(0)

print(f"{OLD_DB} → {LIBRARY_DB} + {LIBRARIAN_DB} 마이그레이션 시작...")

# library.db: books, files만 남김
shutil.copy2(OLD_DB, LIBRARY_DB)
db = sqlite3.connect(LIBRARY_DB)
for table in ["memories", "user_memories", "permanent_memories", "long_term_memories",
              "knowledge", "knowledge_base", "knowledge_learned"]:
    try:
        db.execute(f"DROP TABLE IF EXISTS {table}")
    except Exception:
        pass
db.execute("VACUUM")
db.commit()
db.close()
print(f"  {LIBRARY_DB} 생성 완료 (books, files)")

# librarian.db: 기억/지식만 남김
shutil.copy2(OLD_DB, LIBRARIAN_DB)
db = sqlite3.connect(LIBRARIAN_DB)

# books, files 삭제
for table in ["books", "files"]:
    try:
        db.execute(f"DROP TABLE IF EXISTS {table}")
    except Exception:
        pass

# learned 테이블 생성 + 기존 데이터 통합
db.execute("""
    CREATE TABLE IF NOT EXISTS learned (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        content    TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
""")
for old_table in ["memories", "user_memories", "permanent_memories",
                  "long_term_memories", "knowledge_learned"]:
    try:
        rows = db.execute(f"SELECT content FROM {old_table}").fetchall()
        for row in rows:
            db.execute("INSERT INTO learned (content, created_at) VALUES (?, datetime('now'))", (row[0],))
        db.execute(f"DROP TABLE {old_table}")
        print(f"  {old_table} → learned 통합")
    except Exception:
        pass

# knowledge → knowledge_base
try:
    db.execute("ALTER TABLE knowledge RENAME TO knowledge_base")
    db.execute("DELETE FROM knowledge_base WHERE category = 'user_taught'")
    print("  knowledge → knowledge_base 변환")
except Exception:
    pass

db.execute("VACUUM")
db.commit()
db.close()
print(f"  {LIBRARIAN_DB} 생성 완료 (knowledge_base, learned)")

os.makedirs("backups", exist_ok=True)
shutil.move(OLD_DB, os.path.join("backups", OLD_DB))
print(f"마이그레이션 완료! 기존 {OLD_DB} → backups/{OLD_DB}")
