"""
web_results 테이블에 status 컬럼 추가
- pending: URL만 저장, 인식 중
- done: 인식 완료
- failed: 인식 실패
기존 레코드는 모두 done으로 설정
"""

import aiosqlite
from config import LIBRARIAN_DB_PATH


async def run():
    async with aiosqlite.connect(LIBRARIAN_DB_PATH) as db:
        try:
            await db.execute("ALTER TABLE web_results ADD COLUMN status TEXT NOT NULL DEFAULT 'done'")
            await db.execute("UPDATE web_results SET status = 'done' WHERE status IS NULL")
            await db.commit()
        except Exception as e:
            if "duplicate column" in str(e).lower():
                pass
            else:
                raise
