"""
url_results 테이블 생성
기존 web_results에서 original_url IS NOT NULL인 것들을 url_results로 이전
"""

import aiosqlite
from config import LIBRARIAN_DB_PATH


async def run():
    async with aiosqlite.connect(LIBRARIAN_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS url_results (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized   TEXT NOT NULL,
                original_url TEXT NOT NULL,
                result       TEXT NOT NULL,
                user_name    TEXT,
                status       TEXT NOT NULL DEFAULT 'done',
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_url_normalized ON url_results(normalized)")

        # 기존 web_results에서 URL 데이터 이전
        try:
            cursor = await db.execute(
                "SELECT query, result, user_name, original_url, status, created_at FROM web_results WHERE original_url IS NOT NULL")
            rows = await cursor.fetchall()
            for row in rows:
                query, result, user_name, original_url, status, created_at = row
                if status != "done":
                    continue
                await db.execute("""
                    INSERT OR IGNORE INTO url_results
                    (normalized, original_url, result, user_name, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (query, original_url, result, user_name, status, created_at))
            await db.execute("DELETE FROM web_results WHERE original_url IS NOT NULL")
            if rows:
                print(f"URL 데이터 이전 완료: {len([r for r in rows if r[4] == 'done'])}건")
        except Exception as e:
            print(f"URL 데이터 이전 실패 (컬럼 없을 수 있음): {e}")

        # web_results에서 status/original_url 컬럼 제거 (SQLite는 DROP COLUMN 지원 안 해서 재생성)
        try:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS web_results_new (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    query      TEXT NOT NULL,
                    result     TEXT NOT NULL,
                    user_name  TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await db.execute("""
                INSERT INTO web_results_new (id, query, result, user_name, created_at)
                SELECT id, query, result, user_name, created_at FROM web_results
                WHERE original_url IS NULL OR original_url = ''
            """)
            await db.execute("DROP TABLE web_results")
            await db.execute("ALTER TABLE web_results_new RENAME TO web_results")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_web_query ON web_results(query)")
            print("web_results 컬럼 정리 완료")
        except Exception as e:
            print(f"web_results 컬럼 정리 실패: {e}")

        await db.commit()
