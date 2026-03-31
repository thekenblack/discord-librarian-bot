"""
library.db에 meta 테이블 추가
catalog_updated_at 키로 카탈로그 변경 시각 추적
"""

import aiosqlite
from config import LIBRARY_DB_PATH


async def run():
    async with aiosqlite.connect(LIBRARY_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO meta (key, value) VALUES ('catalog_updated_at', datetime('now'))
        """)
        await db.commit()
