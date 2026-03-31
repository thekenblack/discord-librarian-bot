"""
media_results 테이블에 file_hash 컬럼 추가 (SHA-256)
기존 레코드는 stored_name 기준으로 해시 계산해서 채움
"""

import os
import hashlib
import aiosqlite
from config import LIBRARIAN_DB_PATH, MEDIA_DIR


async def run():
    async with aiosqlite.connect(LIBRARIAN_DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE media_results ADD COLUMN file_hash TEXT")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_media_hash ON media_results(file_hash)")
            await db.commit()
        except Exception as e:
            if "duplicate column" in str(e).lower():
                pass
            else:
                raise

        # 기존 레코드 해시화
        cursor = await db.execute(
            "SELECT id, stored_name FROM media_results WHERE file_hash IS NULL AND stored_name IS NOT NULL")
        rows = await cursor.fetchall()
        count = 0
        for row in rows:
            path = os.path.join(MEDIA_DIR, row[1])
            if os.path.exists(path):
                with open(path, "rb") as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()
                await db.execute(
                    "UPDATE media_results SET file_hash = ? WHERE id = ?",
                    (file_hash, row[0]))
                count += 1
        await db.commit()
        if count:
            print(f"기존 미디어 해시화: {count}건")
