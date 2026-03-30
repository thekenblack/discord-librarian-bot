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

            # 커스텀 지식 (주관적, 커뮤니티별)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS customs (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT,
                    content  TEXT NOT NULL,
                    alias    TEXT
                )
            """)

            # 도서 학습 지식 (파일 내용 기반)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS book_knowledge (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id  INTEGER,
                    content  TEXT NOT NULL,
                    source   TEXT
                )
            """)

            # 웹 검색 결과 캐시
            await db.execute("""
                CREATE TABLE IF NOT EXISTS web_results (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    query      TEXT NOT NULL,
                    result     TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            # 미디어 인식 결과 캐시
            await db.execute("""
                CREATE TABLE IF NOT EXISTS media_results (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename   TEXT NOT NULL,
                    result     TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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

            category_priority = {
                "bitcoin_basics": 95, "bip": 70, "money_history": 80,
                "bitcoin_culture": 75, "bitcoin_people": 75,
                "bitcoin_economics": 65, "bitcoin_tech_deep": 55,
                "bitcoin_technology": 60, "bitcoin_philosophy": 50,
                "bitcoin_history": 50, "bitcoin_lightning": 50,
                "bitcoin_books": 50, "ereader": 35, "austrian_economics": 65,
            }

            total = 0
            for filename in sorted(os.listdir(knowledge_dir)):
                if not filename.endswith(".txt"):
                    continue
                category = filename.replace(".txt", "")
                priority = category_priority.get(category, 50)
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
                                "INSERT INTO knowledge_base (category, alias, content, priority) VALUES (?, ?, ?, ?)",
                                (category, alias, content, priority))
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

    async def search_all(self, keyword: str, limit: int = 10,
                         exclude_memory_ids: list[int] = None,
                         exclude_web_ids: list[int] = None,
                         exclude_media_ids: list[int] = None,
                         user_name: str = None) -> dict:
        """5개 카테고리 검색. 각 카테고리별 limit건."""
        like = f"%{keyword}%"
        like_nospace = f"%{keyword.replace(' ', '')}%"

        mem_exclude = ""
        if exclude_memory_ids:
            mem_exclude = f"AND id NOT IN ({','.join(str(i) for i in exclude_memory_ids)})"
        web_exclude = ""
        if exclude_web_ids:
            web_exclude = f"AND id NOT IN ({','.join(str(i) for i in exclude_web_ids)})"
        media_exclude = ""
        if exclude_media_ids:
            media_exclude = f"AND id NOT IN ({','.join(str(i) for i in exclude_media_ids)})"

        result = {}

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            # 1. 기억 (유저 10건 + 나머지 10건)
            memory_items = []
            if user_name:
                cursor = await db.execute(f"""
                    SELECT author, content FROM learned
                    WHERE (forgotten IS NULL OR forgotten = 0)
                      AND author LIKE ?
                      AND (content LIKE ? OR REPLACE(content, ' ', '') LIKE ?)
                      {mem_exclude}
                    LIMIT ?
                """, (f"{user_name}%", like, like_nospace, limit))
                for r in await cursor.fetchall():
                    memory_items.append(f"{r['author']}: {r['content']}" if r["author"] else r["content"])

            user_exclude = f"AND (author IS NULL OR author NOT LIKE '{user_name}%')" if user_name else ""
            cursor = await db.execute(f"""
                SELECT author, content FROM learned
                WHERE (forgotten IS NULL OR forgotten = 0)
                  AND (content LIKE ? OR REPLACE(content, ' ', '') LIKE ? OR author LIKE ?)
                  {mem_exclude} {user_exclude}
                LIMIT ?
            """, (like, like_nospace, like, limit))
            seen = set(memory_items)
            for r in await cursor.fetchall():
                item = f"{r['author']}: {r['content']}" if r["author"] else r["content"]
                if item not in seen:
                    memory_items.append(item)
            if memory_items:
                result["기억"] = memory_items

            # 2. 지식 (priority 높은 순 → 같으면 랜덤)
            cursor = await db.execute("""
                SELECT content FROM knowledge_base
                WHERE content LIKE ? OR REPLACE(content, ' ', '') LIKE ?
                   OR alias LIKE ? OR REPLACE(alias, ' ', '') LIKE ?
                ORDER BY COALESCE(priority, 50) DESC, RANDOM()
                LIMIT ?
            """, (like, like_nospace, like, like_nospace, limit))
            rows = [r["content"] for r in await cursor.fetchall()]
            if rows:
                result["지식"] = rows

            # 3. 커스텀 (priority 높은 순 → 같으면 랜덤)
            cursor = await db.execute("""
                SELECT content FROM customs
                WHERE content LIKE ? OR REPLACE(content, ' ', '') LIKE ?
                   OR alias LIKE ? OR REPLACE(alias, ' ', '') LIKE ?
                ORDER BY COALESCE(priority, 50) DESC, RANDOM()
                LIMIT ?
            """, (like, like_nospace, like, like_nospace, limit))
            rows = [r["content"] for r in await cursor.fetchall()]
            if rows:
                result["커스텀"] = rows

            # 4. 웹 캐시
            cursor = await db.execute(f"""
                SELECT query, result FROM web_results
                WHERE (query LIKE ? OR result LIKE ?)
                  {web_exclude}
                ORDER BY id DESC LIMIT ?
            """, (like, like, limit))
            rows = [f"[{r['query']}] {r['result']}" for r in await cursor.fetchall()]
            if rows:
                result["웹"] = rows

            # 5. 미디어 캐시
            cursor = await db.execute(f"""
                SELECT id, filename, result, stored_name FROM media_results
                WHERE (filename LIKE ? OR result LIKE ?)
                  {media_exclude}
                ORDER BY id DESC LIMIT ?
            """, (like, like, limit))
            rows = []
            for r in await cursor.fetchall():
                line = f"[media_id:{r['id']}] [{r['filename']}] {r['result']}"
                if r["stored_name"]:
                    line += " (첨부 가능)"
                rows.append(line)
            if rows:
                result["미디어"] = rows

            # 6. 도서 지식
            cursor = await db.execute("""
                SELECT source, content FROM book_knowledge
                WHERE content LIKE ? OR REPLACE(content, ' ', '') LIKE ?
                   OR source LIKE ?
                LIMIT ?
            """, (like, like_nospace, like, limit))
            rows = [f"《{r['source']}》: {r['content']}" if r["source"] else r["content"] for r in await cursor.fetchall()]
            if rows:
                result["도서"] = rows

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

    async def expand_keyword(self, keyword: str) -> tuple[list[str], list[dict]]:
        """키워드의 모든 별칭을 찾아서 검색어 확장. 사용된 별칭 정보도 반환."""
        like = f"%{keyword}%"
        keywords = [keyword]
        aliases_used = []
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, name, alias FROM aliases WHERE name LIKE ?", (like,))
            for row in await cursor.fetchall():
                if row["alias"] not in keywords:
                    keywords.append(row["alias"])
                aliases_used.append({"id": row["id"], "name": row["name"], "alias": row["alias"]})
        return keywords, aliases_used

    async def delete_alias(self, alias_id: int):
        """별칭 삭제 (양방향)"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT name, alias FROM aliases WHERE id = ?", (alias_id,))
            row = await cursor.fetchone()
            if row:
                await db.execute("DELETE FROM aliases WHERE id = ?", (alias_id,))
                await db.execute("DELETE FROM aliases WHERE name = ? AND alias = ?", (row["alias"], row["name"]))
                await db.commit()
                return True
        return False

    # ── 저장 ──────────────────────────────────────────────

    async def forget(self, keyword: str) -> int:
        """키워드에 매칭되는 기억을 soft delete"""
        like = f"%{keyword}%"
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE learned SET forgotten = 1 WHERE content LIKE ? AND (forgotten IS NULL OR forgotten = 0)",
                (like,))
            await db.commit()
            return cursor.rowcount

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
            await db.commit()
            return cursor.lastrowid

    # ── 웹 검색 / 미디어 인식 캐시 ────────────────────────

    async def save_web_result(self, query: str, result: str, user_name: str = None, original_url: str = None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO web_results (query, result, user_name, original_url) VALUES (?, ?, ?, ?)",
                (query, result, user_name, original_url))
            await db.commit()

    async def get_recent_web_results(self, limit: int = 10, user_name: str = None) -> tuple[list[dict], list[dict], list[int]]:
        """유저 것 limit건 + 나머지 limit건 분리 반환 + ID 목록"""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            user_results = []
            if user_name:
                cursor = await db.execute(
                    "SELECT id, query, result FROM web_results WHERE user_name = ? ORDER BY id DESC LIMIT ?",
                    (user_name, limit))
                user_results = [dict(r) for r in await cursor.fetchall()]
            user_queries = {r["query"] for r in user_results}
            exclude = f"AND user_name != '{user_name}'" if user_name else ""
            cursor = await db.execute(f"""
                SELECT id, query, result FROM web_results WHERE 1=1 {exclude} ORDER BY id DESC LIMIT ?
            """, (limit,))
            other_results = [dict(r) for r in await cursor.fetchall() if r["query"] not in user_queries]
            all_ids = [r["id"] for r in user_results] + [r["id"] for r in other_results]
            return user_results, other_results, all_ids

    async def save_media_result(self, filename: str, result: str, user_name: str = None, uploader: str = None, stored_name: str = None) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO media_results (filename, result, user_name, uploader, stored_name) VALUES (?, ?, ?, ?, ?)",
                (filename, result, user_name, uploader, stored_name))
            await db.commit()
            return cursor.lastrowid

    async def save_book_knowledge(self, book_id: int, content: str, source: str = None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO book_knowledge (book_id, content, source) VALUES (?, ?, ?)",
                (book_id, content, source))
            await db.commit()

    async def has_book_knowledge(self, book_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM book_knowledge WHERE book_id = ?",
                (book_id,))
            row = await cursor.fetchone()
            return row[0] > 0

    async def get_media_by_filename(self, filename: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, filename, result, stored_name FROM media_results WHERE filename = ? ORDER BY id DESC LIMIT 1",
                (filename,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_web_by_query(self, query: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, query, result FROM web_results WHERE query = ? ORDER BY id DESC LIMIT 1",
                (query,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_media_by_id(self, media_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, filename, result, stored_name FROM media_results WHERE id = ?",
                (media_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_recent_media_results(self, limit: int = 10, exclude_filenames: list[str] = None, user_name: str = None) -> tuple[list[dict], list[dict], list[int]]:
        """유저 것 limit건 + 나머지 limit건 분리 반환 + ID 목록"""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            exclude_clause = ""
            if exclude_filenames:
                placeholders = ",".join("?" for _ in exclude_filenames)
                exclude_clause = f"AND filename NOT IN ({placeholders})"
            exclude_params = list(exclude_filenames) if exclude_filenames else []

            user_results = []
            if user_name:
                cursor = await db.execute(f"""
                    SELECT id, filename, result FROM media_results
                    WHERE user_name = ? {exclude_clause}
                    ORDER BY id DESC LIMIT ?
                """, (user_name, *exclude_params, limit))
                user_results = [dict(r) for r in await cursor.fetchall()]
            user_files = {r["filename"] for r in user_results}
            user_exclude = f"AND user_name != '{user_name}'" if user_name else ""
            cursor = await db.execute(f"""
                SELECT id, filename, result FROM media_results
                WHERE 1=1 {exclude_clause} {user_exclude}
                ORDER BY id DESC LIMIT ?
            """, (*exclude_params, limit))
            other_results = [dict(r) for r in await cursor.fetchall() if r["filename"] not in user_files]
            all_ids = [r["id"] for r in user_results] + [r["id"] for r in other_results]
            return user_results, other_results, all_ids
