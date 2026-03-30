"""
AI 사서 DB (기초지식 + 학습)
"""

import aiosqlite
import logging
from datetime import datetime, timezone
from config import LIBRARIAN_DB_PATH

logger = logging.getLogger("LibrarianDB")


class LibrarianDB:
    def __init__(self):
        self.path = LIBRARIAN_DB_PATH

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")

            # 기초 지식 (txt에서 로드)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    alias    TEXT,
                    content  TEXT NOT NULL
                )
            """)

            # 학습 (유저가 가르친 것 + 기억)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS learned (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    content    TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

            # 별칭 (검색 확장용, 쌍 기반)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS aliases (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    name  TEXT NOT NULL,
                    alias TEXT NOT NULL
                )
            """)

            # 마이그레이션: 기존 테이블들 → learned로 통합
            for old_table in ["memories", "user_memories", "long_term_memories",
                              "permanent_memories", "knowledge_learned"]:
                try:
                    async with db.execute(f"SELECT content FROM {old_table}") as cursor:
                        rows = await cursor.fetchall()
                        for row in rows:
                            await db.execute(
                                "INSERT INTO learned (content, created_at) VALUES (?, ?)",
                                (row[0], datetime.now(timezone.utc).isoformat()))
                    await db.execute(f"DROP TABLE {old_table}")
                    logger.info(f"마이그레이션: {old_table} → learned")
                except Exception:
                    pass

            # 기존 knowledge → knowledge_base 마이그레이션
            try:
                await db.execute("ALTER TABLE knowledge RENAME TO knowledge_base")
                await db.execute("DELETE FROM knowledge_base WHERE category = 'user_taught'")
                logger.info("knowledge → knowledge_base 마이그레이션 완료")
            except Exception:
                pass

            async def _add_column(table, column, coltype):
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                except Exception:
                    pass

            await _add_column("knowledge_base", "alias", "TEXT")

            # alias_groups → aliases 마이그레이션
            try:
                await db.execute("DROP TABLE IF EXISTS alias_groups")
            except Exception:
                pass

            await db.commit()
            logger.info("사서 DB 초기화 완료")

    # ── 기초 지식 로드 ────────────────────────────────────

    async def load_knowledge_from_files(self, knowledge_dir: str):
        import os
        if not os.path.exists(knowledge_dir):
            return

        line_count = 0
        for filename in sorted(os.listdir(knowledge_dir)):
            if not filename.endswith(".txt"):
                continue
            with open(os.path.join(knowledge_dir, filename), encoding="utf-8") as f:
                line_count += sum(1 for line in f if line.strip())

        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM knowledge_base")
            db_count = (await cursor.fetchone())[0]

            if db_count == line_count and line_count > 0:
                logger.info(f"기초지식 확인 완료: {db_count}건 (변경 없음)")
                return

            await db.execute("DELETE FROM knowledge_base")

            total = 0
            for filename in sorted(os.listdir(knowledge_dir)):
                if not filename.endswith(".txt"):
                    continue
                category = filename.replace(".txt", "")
                filepath = os.path.join(knowledge_dir, filename)
                fc = 0
                with open(filepath, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            if " | " in line:
                                content, alias = line.split(" | ", 1)
                            else:
                                content, alias = line, None
                            await db.execute(
                                "INSERT INTO knowledge_base (category, alias, content) VALUES (?, ?, ?)",
                                (category, alias, content))
                            fc += 1
                logger.info(f"지식 로드: {category} ({fc}건)")
                total += fc
            # 별칭 등록
            await db.execute("DELETE FROM aliases")
            cursor = await db.execute("SELECT content, alias FROM knowledge_base WHERE alias IS NOT NULL")
            alias_count = 0
            for row in await cursor.fetchall():
                main_name = row[0].split(":")[0].split("(")[0].strip()
                aliases = [a.strip() for a in row[1].split(",") if a.strip()]
                for a in aliases:
                    if main_name and a and main_name != a:
                        await db.execute("INSERT INTO aliases (name, alias) VALUES (?, ?)", (main_name, a))
                        await db.execute("INSERT INTO aliases (name, alias) VALUES (?, ?)", (a, main_name))
                        alias_count += 1

            await db.commit()
            logger.info(f"지식 베이스 로드 완료: 총 {total}건, 별칭 {alias_count}쌍")

    async def cleanup_learned(self):
        """쓰레기 학습 데이터 정리"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "DELETE FROM learned WHERE content LIKE '%?' OR content LIKE '%뭐%야' OR content LIKE '%누구%야' OR content LIKE '[웹검색]%'")
            await db.commit()
            if cursor.rowcount > 0:
                logger.info(f"쓰레기 학습 정리: {cursor.rowcount}건 삭제")

    # ── 통합 검색 ─────────────────────────────────────────

    async def search_all(self, keyword: str, limit: int = 10) -> dict:
        """기초지식 + 학습을 통합 검색"""
        like = f"%{keyword}%"
        like_nospace = f"%{keyword.replace(' ', '')}%"
        result = {}

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            # 기초 지식
            cursor = await db.execute("""
                SELECT content FROM knowledge_base
                WHERE content LIKE ? OR REPLACE(content, ' ', '') LIKE ?
                   OR alias LIKE ? OR REPLACE(alias, ' ', '') LIKE ?
                LIMIT ?
            """, (like, like_nospace, like, like_nospace, limit))
            rows = await cursor.fetchall()
            if rows:
                result["지식"] = [r["content"] for r in rows]

            # 학습 (기억 포함)
            cursor = await db.execute("""
                SELECT author, content FROM learned
                WHERE content LIKE ? OR REPLACE(content, ' ', '') LIKE ?
                   OR author LIKE ?
                LIMIT ?
            """, (like, like_nospace, like, limit))
            rows = await cursor.fetchall()
            if rows:
                result["기억"] = [
                    f"{r['author']}: {r['content']}" if r["author"] else r["content"]
                    for r in rows
                ]

        return result

    # ── 별칭 ──────────────────────────────────────────────

    async def add_alias(self, name: str, alias: str):
        """쌍으로 별칭 등록 (양방향, 중복 방지)"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT id FROM aliases WHERE name = ? AND alias = ?", (name, alias))
            if not await cursor.fetchone():
                await db.execute("INSERT INTO aliases (name, alias) VALUES (?, ?)", (name, alias))
                await db.execute("INSERT INTO aliases (name, alias) VALUES (?, ?)", (alias, name))
                await db.commit()

    async def expand_keyword(self, keyword: str) -> list[str]:
        """키워드의 모든 별칭을 찾아서 검색어 확장"""
        like = f"%{keyword}%"
        keywords = [keyword]
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT alias FROM aliases WHERE name LIKE ?", (like,))
            for row in await cursor.fetchall():
                if row["alias"] not in keywords:
                    keywords.append(row["alias"])
        return keywords

    # ── 저장 ──────────────────────────────────────────────

    MAX_LEARNED = 100

    async def save(self, content: str, author: str | None = None) -> int:
        """기억/지식 통합 저장 (중복 방지, 최대 건수 유지)"""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT id FROM learned WHERE content = ?", (content,))
            if await cursor.fetchone():
                return -1
            cursor = await db.execute(
                "INSERT INTO learned (content, author, created_at) VALUES (?, ?, ?)",
                (content, author, now))
            # 오래된 기억 삭제 (최대 건수 초과 시)
            await db.execute(f"""
                DELETE FROM learned WHERE id NOT IN (
                    SELECT id FROM learned ORDER BY id DESC LIMIT {self.MAX_LEARNED}
                )
            """)
            await db.commit()
            return cursor.lastrowid
