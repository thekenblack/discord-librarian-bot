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
                    source   TEXT,
                    status   TEXT NOT NULL DEFAULT 'done'
                )
            """)

            # 웹 검색 결과 캐시 (키워드 검색)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS web_results (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    query      TEXT NOT NULL,
                    result     TEXT NOT NULL,
                    user_name  TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            # URL 인식 결과 캐시
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

            # 미디어 인식 결과 캐시
            await db.execute("""
                CREATE TABLE IF NOT EXISTS media_results (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename   TEXT NOT NULL,
                    result     TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            # 유저별 감정 (user_friendly, user_lovely, user_trust)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_emotion (
                    user_id    TEXT PRIMARY KEY,
                    user_name  TEXT,
                    friendly   REAL NOT NULL DEFAULT 5,
                    lovely     REAL NOT NULL DEFAULT 5,
                    trust      REAL NOT NULL DEFAULT 5,
                    interaction_count INTEGER NOT NULL DEFAULT 0,
                    last_interaction TEXT
                )
            """)

            # 공통/전역 감정 (self_mood, self_energy, server_vibe)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_emotion (
                    key   TEXT PRIMARY KEY,
                    value REAL NOT NULL DEFAULT 5,
                    updated_at TEXT
                )
            """)
            for key in ("self_mood", "self_energy", "server_vibe"):
                await db.execute(
                    "INSERT OR IGNORE INTO bot_emotion (key, value) VALUES (?, 5)", (key,))

            # 감정 변동 기록
            await db.execute("""
                CREATE TABLE IF NOT EXISTS emotion_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    target     TEXT,
                    user_name  TEXT,
                    changes    TEXT NOT NULL,
                    reason     TEXT,
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
            await _add_column("knowledge_base", "priority", "INTEGER DEFAULT 50")
            await _add_column("learned", "author", "TEXT")
            await _add_column("learned", "forgotten", "INTEGER DEFAULT 0")
            await _add_column("web_results", "user_name", "TEXT")
            await _add_column("media_results", "user_name", "TEXT")
            await _add_column("media_results", "uploader", "TEXT")
            await _add_column("media_results", "stored_name", "TEXT")
            await _add_column("media_results", "file_hash", "TEXT")
            await _add_column("book_knowledge", "status", "TEXT DEFAULT 'done'")

            # 인덱스
            await db.execute("CREATE INDEX IF NOT EXISTS idx_media_hash ON media_results(file_hash)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_web_query ON web_results(query)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_url_normalized ON url_results(normalized)")

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
                         exclude_url_ids: list[int] = None,
                         exclude_media_ids: list[int] = None,
                         user_name: str = None) -> dict:
        """7개 카테고리 검색. 각 카테고리별 limit건."""
        like = f"%{keyword}%"
        like_nospace = f"%{keyword.replace(' ', '')}%"

        mem_exclude = ""
        if exclude_memory_ids:
            mem_exclude = f"AND id NOT IN ({','.join(str(i) for i in exclude_memory_ids)})"
        web_exclude = ""
        if exclude_web_ids:
            web_exclude = f"AND id NOT IN ({','.join(str(i) for i in exclude_web_ids)})"
        url_exclude = ""
        if exclude_url_ids:
            url_exclude = f"AND id NOT IN ({','.join(str(i) for i in exclude_url_ids)})"
        media_exclude = ""
        if exclude_media_ids:
            media_exclude = f"AND id NOT IN ({','.join(str(i) for i in exclude_media_ids)})"

        def _snippet(text: str) -> str:
            """키워드 주변 200자 추출. 없으면 앞 200자."""
            lower = text.lower()
            lower_kw = keyword.lower()
            idx = lower.find(lower_kw)
            if idx >= 0:
                start = max(0, idx - 100)
                end = min(len(text), idx + len(keyword) + 100)
                s = text[start:end]
                if start > 0:
                    s = "..." + s
                if end < len(text):
                    s = s + "..."
                return s
            return text[:200]

        result = {}

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            # 1. 기억 (유저 limit건 + 나머지 limit건)
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
                    item = f"{r['author']}: {r['content']}" if r["author"] else r["content"]
                    memory_items.append(_snippet(item))

            if user_name:
                cursor = await db.execute(f"""
                    SELECT author, content FROM learned
                    WHERE (forgotten IS NULL OR forgotten = 0)
                      AND (author IS NULL OR author NOT LIKE ?)
                      AND (content LIKE ? OR REPLACE(content, ' ', '') LIKE ? OR author LIKE ?)
                      {mem_exclude}
                    LIMIT ?
                """, (f"{user_name}%", like, like_nospace, like, limit))
            else:
                cursor = await db.execute(f"""
                    SELECT author, content FROM learned
                    WHERE (forgotten IS NULL OR forgotten = 0)
                      AND (content LIKE ? OR REPLACE(content, ' ', '') LIKE ? OR author LIKE ?)
                      {mem_exclude}
                    LIMIT ?
                """, (like, like_nospace, like, limit))
            seen = set(memory_items)
            for r in await cursor.fetchall():
                item = f"{r['author']}: {r['content']}" if r["author"] else r["content"]
                item = _snippet(item)
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
            rows = [_snippet(r["content"]) for r in await cursor.fetchall()]
            if rows:
                result["지식"] = rows

            # 3. 커스텀
            cursor = await db.execute("""
                SELECT content FROM customs
                WHERE content LIKE ? OR REPLACE(content, ' ', '') LIKE ?
                   OR alias LIKE ? OR REPLACE(alias, ' ', '') LIKE ?
                ORDER BY COALESCE(priority, 50) DESC, RANDOM()
                LIMIT ?
            """, (like, like_nospace, like, like_nospace, limit))
            rows = [_snippet(r["content"]) for r in await cursor.fetchall()]
            if rows:
                result["커스텀"] = rows

            # 4. 웹 검색 캐시
            cursor = await db.execute(f"""
                SELECT query, result FROM web_results
                WHERE (query LIKE ? OR result LIKE ?)
                  {web_exclude}
                ORDER BY id DESC LIMIT ?
            """, (like, like, limit))
            rows = [f"[{r['query']}] {_snippet(r['result'])}" for r in await cursor.fetchall()]
            if rows:
                result["웹"] = rows

            # 5. URL 인식 캐시
            cursor = await db.execute(f"""
                SELECT normalized, original_url, result FROM url_results
                WHERE (result LIKE ? OR original_url LIKE ? OR normalized LIKE ?)
                  AND status = 'done'
                  {url_exclude}
                ORDER BY id DESC LIMIT ?
            """, (like, like, like, limit))
            rows = [f"[{r['original_url']}] {_snippet(r['result'])}" for r in await cursor.fetchall()]
            if rows:
                result["URL"] = rows

            # 6. 미디어 캐시
            cursor = await db.execute(f"""
                SELECT id, filename, result, stored_name FROM media_results
                WHERE (filename LIKE ? OR result LIKE ?)
                  {media_exclude}
                ORDER BY id DESC LIMIT ?
            """, (like, like, limit))
            rows = []
            for r in await cursor.fetchall():
                line = f"[media_id:{r['id']}] [{r['filename']}] {_snippet(r['result'])}"
                if r["stored_name"]:
                    line += " (첨부 가능)"
                rows.append(line)
            if rows:
                result["미디어"] = rows

            # 7. URL 캐시
            cursor = await db.execute(f"""
                SELECT id, normalized, original_url, result FROM url_results
                WHERE status = 'done'
                  AND (original_url LIKE ? OR result LIKE ? OR normalized LIKE ?)
                  {url_exclude}
                ORDER BY id DESC LIMIT ?
            """, (like, like, like, limit))
            rows = []
            for r in await cursor.fetchall():
                line = f"[url_id:{r['id']}] [{r['original_url']}] {_snippet(r['result'])}"
                rows.append(line)
            if rows:
                result["URL"] = rows

            # 8. 도서 지식 (키워드 주변 200자, done만)
            cursor = await db.execute("""
                SELECT source, content FROM book_knowledge
                WHERE status = 'done'
                  AND (content LIKE ? OR REPLACE(content, ' ', '') LIKE ?
                       OR source LIKE ?)
                LIMIT ?
            """, (like, like_nospace, like, limit))
            rows = []
            for r in await cursor.fetchall():
                prefix = f"《{r['source']}》: " if r["source"] else ""
                rows.append(prefix + _snippet(r["content"]))
            if rows:
                result["도서"] = rows

        return result

    # ── 별칭 ──────────────────────────────────────────────

    async def save_alias(self, name: str, alias: str):
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

    # ── 웹 검색 캐시 ──────────────────────────────────────

    async def save_web_result(self, query: str, result: str, user_name: str = None) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT id FROM web_results WHERE query = ? ORDER BY id DESC LIMIT 1", (query,))
            existing = await cursor.fetchone()
            if existing:
                return existing[0]
            cursor = await db.execute(
                "INSERT INTO web_results (query, result, user_name) VALUES (?, ?, ?)",
                (query, result, user_name))
            await db.commit()
            return cursor.lastrowid

    async def get_web_by_query(self, query: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, query, result FROM web_results WHERE query = ? ORDER BY id DESC LIMIT 1",
                (query,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_recent_web_results(self, limit: int = 5, user_name: str = None) -> tuple[list[dict], list[dict], list[int]]:
        """유저 것 + 타인 것 분리 반환."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            user_results = []
            if user_name:
                cursor = await db.execute(
                    "SELECT id, query, result FROM web_results WHERE user_name = ? ORDER BY id DESC LIMIT ?",
                    (user_name, limit))
                user_results = [dict(r) for r in await cursor.fetchall()]
            user_queries = {r["query"] for r in user_results}
            if user_name:
                cursor = await db.execute(
                    "SELECT id, query, result FROM web_results WHERE (user_name IS NULL OR user_name != ?) ORDER BY id DESC LIMIT ?",
                    (user_name, limit))
            else:
                cursor = await db.execute(
                    "SELECT id, query, result FROM web_results ORDER BY id DESC LIMIT ?", (limit,))
            other_results = [dict(r) for r in await cursor.fetchall() if r["query"] not in user_queries]
            all_ids = [r["id"] for r in user_results] + [r["id"] for r in other_results]
            return user_results, other_results, all_ids

    # ── URL 인식 캐시 ──────────────────────────────────────

    async def save_url_result(self, normalized: str, original_url: str, result: str = "",
                              user_name: str = None, status: str = "done") -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT id, status FROM url_results WHERE normalized = ? ORDER BY id DESC LIMIT 1",
                (normalized,))
            existing = await cursor.fetchone()
            if existing and existing[1] != "failed":
                return existing[0]
            cursor = await db.execute(
                "INSERT INTO url_results (normalized, original_url, result, user_name, status) VALUES (?, ?, ?, ?, ?)",
                (normalized, original_url, result, user_name, status))
            await db.commit()
            return cursor.lastrowid

    async def update_url_result(self, normalized: str, result: str, status: str = "done"):
        """pending/failed 상태 레코드를 업데이트"""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE url_results SET result = ?, status = ? WHERE normalized = ? AND status IN ('pending', 'failed')",
                (result, status, normalized))
            await db.commit()

    async def get_url_by_id(self, url_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, normalized, original_url, result, status FROM url_results WHERE id = ?",
                (url_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_url_by_normalized(self, normalized: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, normalized, original_url, result, status FROM url_results WHERE normalized = ? ORDER BY id DESC LIMIT 1",
                (normalized,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_recent_url_results(self, limit: int = 5, user_name: str = None) -> tuple[list[dict], list[dict], list[int]]:
        """done인 것만. 유저 것 + 타인 것 분리 반환."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            user_results = []
            if user_name:
                cursor = await db.execute(
                    "SELECT id, normalized, original_url, result FROM url_results WHERE user_name = ? AND status = 'done' ORDER BY id DESC LIMIT ?",
                    (user_name, limit))
                user_results = [dict(r) for r in await cursor.fetchall()]
            user_urls = {r["normalized"] for r in user_results}
            if user_name:
                cursor = await db.execute(
                    "SELECT id, normalized, original_url, result FROM url_results WHERE (user_name IS NULL OR user_name != ?) AND status = 'done' ORDER BY id DESC LIMIT ?",
                    (user_name, limit))
            else:
                cursor = await db.execute(
                    "SELECT id, normalized, original_url, result FROM url_results WHERE status = 'done' ORDER BY id DESC LIMIT ?",
                    (limit,))
            other_results = [dict(r) for r in await cursor.fetchall() if r["normalized"] not in user_urls]
            all_ids = [r["id"] for r in user_results] + [r["id"] for r in other_results]
            return user_results, other_results, all_ids

    async def reset_stale_url_results(self):
        """시작 시 failed/pending 삭제"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "DELETE FROM url_results WHERE status IN ('failed', 'pending')")
            await db.commit()
            if cursor.rowcount > 0:
                logger.info(f"stale URL 결과 초기화: {cursor.rowcount}건 삭제")

    # ── 미디어 인식 캐시 ──────────────────────────────────

    async def save_media_result(self, filename: str, result: str, user_name: str = None,
                                uploader: str = None, stored_name: str = None,
                                file_hash: str = None) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO media_results (filename, result, user_name, uploader, stored_name, file_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (filename, result, user_name, uploader, stored_name, file_hash))
            await db.commit()
            return cursor.lastrowid

    async def get_media_by_filename(self, filename: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, filename, result, stored_name FROM media_results WHERE filename = ? ORDER BY id DESC LIMIT 1",
                (filename,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_media_by_hash(self, file_hash: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, filename, result, stored_name FROM media_results WHERE file_hash = ? ORDER BY id DESC LIMIT 1",
                (file_hash,))
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

    async def get_recent_media_results(self, limit: int = 10, exclude_filenames: list[str] = None,
                                       user_name: str = None) -> tuple[list[dict], list[dict], list[int]]:
        """유저 것 limit건 + 나머지 limit건 분리 반환 + ID 목록"""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            exclude_clause = ""
            exclude_params = []
            if exclude_filenames:
                placeholders = ",".join("?" for _ in exclude_filenames)
                exclude_clause = f"AND filename NOT IN ({placeholders})"
                exclude_params = list(exclude_filenames)

            user_results = []
            if user_name:
                cursor = await db.execute(
                    f"SELECT id, filename, result FROM media_results WHERE user_name = ? {exclude_clause} ORDER BY id DESC LIMIT ?",
                    (user_name, *exclude_params, limit))
                user_results = [dict(r) for r in await cursor.fetchall()]
            user_files = {r["filename"] for r in user_results}

            if user_name:
                cursor = await db.execute(
                    f"SELECT id, filename, result FROM media_results WHERE (user_name IS NULL OR user_name != ?) {exclude_clause} ORDER BY id DESC LIMIT ?",
                    (user_name, *exclude_params, limit))
            else:
                cursor = await db.execute(
                    f"SELECT id, filename, result FROM media_results WHERE 1=1 {exclude_clause} ORDER BY id DESC LIMIT ?",
                    (*exclude_params, limit))
            other_results = [dict(r) for r in await cursor.fetchall() if r["filename"] not in user_files]
            all_ids = [r["id"] for r in user_results] + [r["id"] for r in other_results]
            return user_results, other_results, all_ids

    # ── 도서 지식 ─────────────────────────────────────────

    async def save_book_knowledge(self, book_id: int, content: str, source: str = None, status: str = "done"):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO book_knowledge (book_id, content, source, status) VALUES (?, ?, ?, ?)",
                (book_id, content, source, status))
            await db.commit()

    async def update_book_knowledge(self, book_id: int, content: str, status: str = "done"):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE book_knowledge SET content = ?, status = ? WHERE book_id = ?",
                (content, status, book_id))
            await db.commit()

    async def has_book_knowledge(self, book_id: int) -> bool:
        """done 또는 pending 상태가 있으면 True"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM book_knowledge WHERE book_id = ? AND status IN ('done', 'pending')",
                (book_id,))
            row = await cursor.fetchone()
            return row[0] > 0

    async def reset_stale_book_knowledge(self):
        """pending/failed 상태 정리 (재시작 시)"""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM book_knowledge WHERE status IN ('pending', 'failed')")
            await db.commit()

    # ── 감정 시스템 v2 ─────────────────────────────────────

    USER_AXES = ["friendly", "lovely", "trust"]
    SELF_AXES = ["self_mood", "self_energy"]
    SERVER_AXES = ["server_vibe"]
    ALL_AXES = USER_AXES + SELF_AXES + SERVER_AXES

    # 1회 변화량 제한: (상한, 하한)
    AXIS_LIMITS = {
        "friendly":    (+3, -3),
        "lovely":      (+1, -3),
        "trust":       (+1, -3),
        "self_mood":   (+3, -3),
        "self_energy":  (+3, -3),
        "server_vibe": (+3, -3),
    }

    async def get_user_emotion(self, user_id: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM user_emotion WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_user_emotions_bulk(self, user_ids: set[str]) -> dict[str, dict]:
        """여러 유저 감정 한 번에 조회"""
        if not user_ids:
            return {}
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            placeholders = ",".join("?" for _ in user_ids)
            cursor = await db.execute(
                f"SELECT * FROM user_emotion WHERE user_id IN ({placeholders})",
                tuple(user_ids))
            return {row["user_id"]: dict(row) for row in await cursor.fetchall()}

    async def get_bot_emotion(self) -> dict:
        """self_mood, self_energy, server_vibe 조회"""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT key, value FROM bot_emotion")
            return {row["key"]: row["value"] for row in await cursor.fetchall()}

    async def update_emotion(self, changes: dict, target_user_id: str = None,
                             target_user_name: str = None, reason: str = None) -> dict:
        """감정 변화 적용. user_ 축은 target 유저에, self_/server_ 축은 전역에."""
        import json
        result = {}

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            # user_ 축 처리
            user_changes = {k: v for k, v in changes.items() if k in self.USER_AXES}
            if user_changes and target_user_id:
                cursor = await db.execute(
                    "SELECT * FROM user_emotion WHERE user_id = ?", (target_user_id,))
                row = await cursor.fetchone()
                current = dict(row) if row else {"friendly": 5, "lovely": 5, "trust": 5,
                                                  "interaction_count": 0}

                for axis, delta in user_changes.items():
                    hi, lo = self.AXIS_LIMITS.get(axis, (+3, -3))
                    delta = max(lo, min(hi, delta))
                    val = current.get(axis, 0) + delta
                    current[axis] = max(0, min(10, val))

                # lovely는 항상 trust 이하
                if current["lovely"] > current["trust"]:
                    current["lovely"] = current["trust"]

                current["interaction_count"] = current.get("interaction_count", 0) + 1
                current["last_interaction"] = datetime.now(timezone.utc).isoformat()

                await db.execute("""
                    INSERT INTO user_emotion (user_id, user_name, friendly, lovely, trust, interaction_count, last_interaction)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        user_name=excluded.user_name,
                        friendly=excluded.friendly, lovely=excluded.lovely, trust=excluded.trust,
                        interaction_count=excluded.interaction_count,
                        last_interaction=excluded.last_interaction
                """, (target_user_id, target_user_name,
                      current["friendly"], current["lovely"], current["trust"],
                      current["interaction_count"], current["last_interaction"]))

                for axis in self.USER_AXES:
                    result[f"user_{axis}"] = current[axis]

            # self_/server_ 축 처리
            global_changes = {k: v for k, v in changes.items()
                              if k in self.SELF_AXES + self.SERVER_AXES}
            for axis, delta in global_changes.items():
                hi, lo = self.AXIS_LIMITS.get(axis, (+3, -3))
                delta = max(lo, min(hi, delta))
                cursor = await db.execute(
                    "SELECT value FROM bot_emotion WHERE key = ?", (axis,))
                row = await cursor.fetchone()
                old_val = row["value"] if row else 0
                new_val = max(0, min(10, old_val + delta))
                await db.execute(
                    "UPDATE bot_emotion SET value = ?, updated_at = ? WHERE key = ?",
                    (new_val, datetime.now(timezone.utc).isoformat(), axis))

            # 항상 전체 상태 반환
            if target_user_id:
                cursor = await db.execute(
                    "SELECT * FROM user_emotion WHERE user_id = ?", (target_user_id,))
                row = await cursor.fetchone()
                if row:
                    for axis in self.USER_AXES:
                        result[f"user_{axis}"] = row[axis]
            cursor = await db.execute("SELECT key, value FROM bot_emotion")
            for row in await cursor.fetchall():
                result[row["key"]] = row["value"]

            # 로그
            changes_str = json.dumps(changes, ensure_ascii=False)
            await db.execute(
                "INSERT INTO emotion_log (target, user_name, changes, reason) VALUES (?, ?, ?, ?)",
                (target_user_id or "self", target_user_name or "self", changes_str, reason))

            await db.commit()
            return result

    async def get_emotion_log(self, target: str = None, limit: int = 5) -> list[dict]:
        """감정 변동 기록 조회."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if target:
                cursor = await db.execute(
                    "SELECT user_name, changes, reason, created_at FROM emotion_log WHERE target = ? ORDER BY id DESC LIMIT ?",
                    (target, limit))
            else:
                cursor = await db.execute(
                    "SELECT user_name, changes, reason, created_at FROM emotion_log ORDER BY id DESC LIMIT ?",
                    (limit,))
            return [dict(r) for r in await cursor.fetchall()]
