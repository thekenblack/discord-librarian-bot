"""
AI 사서 DB (기초지식 + 학습)
"""

import asyncio
import aiosqlite
import logging
from datetime import datetime, timezone
from config import LIBRARIAN_DB_PATH

logger = logging.getLogger("LibrarianDB")


class LibrarianDB:
    def __init__(self):
        self.path = LIBRARIAN_DB_PATH
        self.vector_store = None  # VectorStore (core.py에서 주입)

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

            # 유저별 감정 (user_comfort, user_affinity, user_trust)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_emotion (
                    user_id    TEXT PRIMARY KEY,
                    user_name  TEXT,
                    comfort   REAL NOT NULL DEFAULT 5,
                    affinity     REAL NOT NULL DEFAULT 5,
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
                    "INSERT OR IGNORE INTO bot_emotion (key, value) VALUES (?, 50)", (key,))
            for key in ("fullness", "hydration"):
                await db.execute(
                    "INSERT OR IGNORE INTO bot_emotion (key, value) VALUES (?, 50)", (key,))

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

            # Evaluation 피드백 (유저별 최신 1건)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_feedback (
                    user_id    TEXT PRIMARY KEY,
                    feedback   TEXT NOT NULL,
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

            # 선물 대기열 (라이브러리 봇 → 사서봇 전달용)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pending_gifts (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    buyer_id   TEXT NOT NULL,
                    item_id    TEXT NOT NULL,
                    item_name  TEXT NOT NULL,
                    item_emoji TEXT NOT NULL,
                    effects    TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            # 선물 기록 (영구 보관, 프롬프트 맥락용)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS gift_log (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    buyer_id       TEXT NOT NULL,
                    buyer_name     TEXT NOT NULL,
                    recipient_id   TEXT,
                    recipient_name TEXT,
                    item_emoji     TEXT NOT NULL,
                    item_name      TEXT NOT NULL,
                    item_price     INTEGER NOT NULL,
                    message        TEXT,
                    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            # 마이그레이션: recipient 컬럼
            for col in ("recipient_id TEXT", "recipient_name TEXT"):
                try:
                    await db.execute(f"ALTER TABLE gift_log ADD COLUMN {col}")
                except Exception:
                    pass

            # 유저별 대화 요약 (L5가 갱신, L1이 읽음)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_summary (
                    user_id    TEXT PRIMARY KEY,
                    summary    TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            # 채널별 흐름 요약 (L5가 갱신, L1이 읽음)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS channel_summary (
                    channel_id TEXT PRIMARY KEY,
                    summary    TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            # 메시지 로그 (맥락 수집용)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS message_log (
                    message_id   TEXT PRIMARY KEY,
                    channel_id   TEXT NOT NULL,
                    author_id    TEXT NOT NULL,
                    author_name  TEXT NOT NULL,
                    content      TEXT NOT NULL DEFAULT '',
                    reference_id TEXT,
                    is_bot       INTEGER NOT NULL DEFAULT 0,
                    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_msglog_channel ON message_log(channel_id, created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_msglog_ref ON message_log(reference_id)")

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
            await _add_column("emotion_log", "message_id", "TEXT")

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

    async def search_all(self, keyword: str, limit: int = 3,
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
                result["웹검색"] = rows

            # 5. URL 인식 캐시 (유튜브 / 일반 URL 분리)
            cursor = await db.execute(f"""
                SELECT id, normalized, original_url, result FROM url_results
                WHERE status = 'done'
                  AND (original_url LIKE ? OR result LIKE ? OR normalized LIKE ?)
                  {url_exclude}
                ORDER BY id DESC LIMIT ?
            """, (like, like, like, limit))
            yt_rows = []
            url_rows = []
            for r in await cursor.fetchall():
                norm = r['normalized'] or ""
                line = f"[url_id:{r['id']}] [{r['original_url']}] {_snippet(r['result'])} (첨부 가능)"
                if norm.startswith("youtu.be/") or norm.startswith("youtube:"):
                    yt_rows.append(line)
                else:
                    url_rows.append(line)
            if yt_rows:
                result["유튜브"] = yt_rows
            if url_rows:
                result["URL"] = url_rows

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

            # 7. 도서 지식 (키워드 주변 200자, done만)
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

            # 8. 유저 감정 (이름으로 검색)
            cursor = await db.execute("""
                SELECT user_id, user_name, comfort, affinity, trust,
                       interaction_count, last_interaction
                FROM user_emotion
                WHERE user_name LIKE ?
                LIMIT ?
            """, (like, limit))
            emo_rows = []
            for r in await cursor.fetchall():
                last_time = r["last_interaction"]
                c = round(self._apply_decay(r["comfort"], "comfort", last_time), 1)
                a = round(self._apply_decay(r["affinity"], "affinity", last_time), 1)
                t = round(self._apply_decay(r["trust"], "trust", last_time), 1)
                emo_rows.append(
                    f"{r['user_name']}: comfort:{c} affinity:{a} trust:{t}"
                    f" (대화 {r['interaction_count']}회)")
            if emo_rows:
                result["유저감정"] = emo_rows

        # 벡터 검색 결과로 덮어쓰기 (4개 카테고리)
        if self.vector_store:
            try:
                vr = await self._search_vector(keyword, limit, _snippet)
                for key in ("기억", "지식", "커스텀", "도서"):
                    if key in vr:
                        result[key] = vr[key]
            except Exception as e:
                logger.warning(f"벡터 검색 실패 (LIKE 결과 유지): {e}")

        return result

    async def _search_vector(self, keyword: str, limit: int, _snippet) -> dict:
        """벡터 검색: 기억, 지식, 커스텀, 도서"""
        vs = self.vector_store
        result = {}

        # 1. 기억 (learned)
        items = await asyncio.to_thread(vs.search, "learned", keyword, limit * 2)
        if items:
            memory_items = []
            for r in items:
                author = (r.get("metadata") or {}).get("author", "")
                doc = r["document"]
                text = f"{author}: {doc}" if author else doc
                memory_items.append(_snippet(text))
            if memory_items:
                result["기억"] = memory_items[:limit]

        # 2. 지식 (knowledge)
        items = await asyncio.to_thread(vs.search, "knowledge", keyword, limit)
        if items:
            result["지식"] = [_snippet(r["document"]) for r in items]

        # 3. 커스텀 (customs)
        items = await asyncio.to_thread(vs.search, "customs", keyword, limit)
        if items:
            result["커스텀"] = [_snippet(r["document"]) for r in items]

        # 4. 도서 (book_knowledge)
        items = await asyncio.to_thread(vs.search, "book_knowledge", keyword, limit)
        if items:
            rows = []
            for r in items:
                source = (r.get("metadata") or {}).get("source", "")
                prefix = f"《{source}》: " if source else ""
                rows.append(prefix + _snippet(r["document"]))
            result["도서"] = rows

        return result

    async def sync_vector_store(self):
        """SQLite → ChromaDB 동기화 (최초 실행 또는 건수 불일치 시)"""
        vs = self.vector_store
        if not vs:
            return

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            # knowledge
            cursor = await db.execute("SELECT COUNT(*) FROM knowledge_base")
            db_count = (await cursor.fetchone())[0]
            if db_count != vs.count("knowledge"):
                vs.reset("knowledge")
                cursor = await db.execute(
                    "SELECT id, category, content, priority FROM knowledge_base")
                rows = await cursor.fetchall()
                if rows:
                    await asyncio.to_thread(
                        vs.add_batch, "knowledge",
                        [f"k_{r['id']}" for r in rows],
                        [r["content"] for r in rows],
                        [{"category": r["category"] or "",
                          "priority": r["priority"] or 50} for r in rows])
                logger.info(f"벡터 동기화: knowledge {len(rows)}건")

            # learned (forgotten 제외)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM learned WHERE forgotten IS NULL OR forgotten = 0")
            db_count = (await cursor.fetchone())[0]
            if db_count != vs.count("learned"):
                vs.reset("learned")
                cursor = await db.execute(
                    "SELECT id, content, author FROM learned "
                    "WHERE forgotten IS NULL OR forgotten = 0")
                rows = await cursor.fetchall()
                if rows:
                    await asyncio.to_thread(
                        vs.add_batch, "learned",
                        [f"l_{r['id']}" for r in rows],
                        [r["content"] for r in rows],
                        [{"author": r["author"] or ""} for r in rows])
                logger.info(f"벡터 동기화: learned {len(rows)}건")

            # customs
            cursor = await db.execute("SELECT COUNT(*) FROM customs")
            db_count = (await cursor.fetchone())[0]
            if db_count != vs.count("customs"):
                vs.reset("customs")
                cursor = await db.execute(
                    "SELECT id, category, content FROM customs")
                rows = await cursor.fetchall()
                if rows:
                    await asyncio.to_thread(
                        vs.add_batch, "customs",
                        [f"c_{r['id']}" for r in rows],
                        [r["content"] for r in rows],
                        [{"category": r["category"] or ""} for r in rows])
                logger.info(f"벡터 동기화: customs {len(rows)}건")

            # book_knowledge (done만)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM book_knowledge WHERE status = 'done'")
            db_count = (await cursor.fetchone())[0]
            if db_count != vs.count("book_knowledge"):
                vs.reset("book_knowledge")
                cursor = await db.execute(
                    "SELECT id, source, content FROM book_knowledge "
                    "WHERE status = 'done'")
                rows = await cursor.fetchall()
                if rows:
                    await asyncio.to_thread(
                        vs.add_batch, "book_knowledge",
                        [f"b_{r['id']}" for r in rows],
                        [r["content"] for r in rows],
                        [{"source": r["source"] or ""} for r in rows])
                logger.info(f"벡터 동기화: book_knowledge {len(rows)}건")

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
            # 벡터 스토어용 ID 먼저 확보
            affected_ids = []
            if self.vector_store:
                cursor = await db.execute(
                    "SELECT id FROM learned WHERE content LIKE ? AND (forgotten IS NULL OR forgotten = 0)",
                    (like,))
                affected_ids = [row[0] for row in await cursor.fetchall()]

            cursor = await db.execute(
                "UPDATE learned SET forgotten = 1 WHERE content LIKE ? AND (forgotten IS NULL OR forgotten = 0)",
                (like,))
            await db.commit()
            count = cursor.rowcount

        # 벡터 스토어에서 제거
        if self.vector_store and affected_ids:
            for row_id in affected_ids:
                try:
                    await asyncio.to_thread(
                        self.vector_store.remove, "learned", f"l_{row_id}")
                except Exception:
                    pass

        return count

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
            new_id = cursor.lastrowid

        # 벡터 스토어에 추가
        if self.vector_store and new_id and new_id > 0:
            try:
                await asyncio.to_thread(
                    self.vector_store.add, "learned", f"l_{new_id}",
                    content, {"author": author or ""})
            except Exception as e:
                logger.warning(f"벡터 저장 실패 (learned): {e}")

        return new_id

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

    async def update_media_result(self, filename: str, result: str):
        """백그라운드 인식 완료 후 결과 업데이트."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE media_results SET result = ? WHERE filename = ? ORDER BY id DESC LIMIT 1",
                (result, filename))
            await db.commit()

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

        if self.vector_store and status == "done" and content:
            try:
                await asyncio.to_thread(
                    self.vector_store.add, "book_knowledge", f"b_{book_id}",
                    content, {"source": source or ""})
            except Exception as e:
                logger.warning(f"벡터 저장 실패 (book): {e}")

    async def update_book_knowledge(self, book_id: int, content: str, status: str = "done"):
        source = ""
        async with aiosqlite.connect(self.path) as db:
            if self.vector_store and status == "done":
                cursor = await db.execute(
                    "SELECT source FROM book_knowledge WHERE book_id = ?", (book_id,))
                row = await cursor.fetchone()
                source = row[0] if row else ""
            await db.execute(
                "UPDATE book_knowledge SET content = ?, status = ? WHERE book_id = ?",
                (content, status, book_id))
            await db.commit()

        if self.vector_store:
            try:
                if status == "done" and content:
                    await asyncio.to_thread(
                        self.vector_store.add, "book_knowledge", f"b_{book_id}",
                        content, {"source": source})
                else:
                    await asyncio.to_thread(
                        self.vector_store.remove, "book_knowledge", f"b_{book_id}")
            except Exception:
                pass

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

    USER_AXES = ["comfort", "affinity", "trust"]
    SELF_AXES = ["self_mood", "self_energy"]
    NEEDS_AXES = ["fullness", "hydration"]  # 물리적 상태 (feel과 별개)
    SERVER_AXES = ["server_vibe"]
    ALL_AXES = USER_AXES + SELF_AXES + SERVER_AXES + NEEDS_AXES

    AXIS_DELTA_MAX = 15  # 보정 후 최종 변화 허용치 ±15
    NEUTRAL = 50  # 중립값
    AXIS_RANGE = (0, 100)
    # 시간 감쇠 반감기 (초)
    DECAY_HALFLIFE = {
        "comfort": 48 * 3600,    # 48시간
        "affinity": 48 * 3600,
        "trust": 48 * 3600,
        "self_mood": 6 * 3600,    # 6시간
        "self_energy": 6 * 3600,
        "server_vibe": 12 * 3600, # 12시간
    }

    def _apply_decay(self, value: float, axis: str, last_time_str: str | None) -> float:
        """시간 감쇠 적용. 중립(50)을 향해 반감기 기반 복귀."""
        if not last_time_str or axis not in self.DECAY_HALFLIFE:
            return value
        try:
            from datetime import datetime, timezone
            import math
            last = datetime.fromisoformat(last_time_str)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed <= 0:
                return value
            halflife = self.DECAY_HALFLIFE[axis]
            decay = math.pow(0.5, elapsed / halflife)
            return self.NEUTRAL + (value - self.NEUTRAL) * decay
        except Exception:
            return value

    async def get_user_emotion(self, user_id: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM user_emotion WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            last_time = result.get("last_interaction")
            for axis in self.USER_AXES:
                result[axis] = round(self._apply_decay(result[axis], axis, last_time), 1)
            return result

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
            results = {}
            for row in await cursor.fetchall():
                d = dict(row)
                last_time = d.get("last_interaction")
                for axis in self.USER_AXES:
                    d[axis] = round(self._apply_decay(d[axis], axis, last_time), 1)
                results[d["user_id"]] = d
            return results

    async def get_bot_emotion(self) -> dict:
        """self_mood, self_energy, server_vibe 조회 (감쇠 적용)"""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT key, value, updated_at FROM bot_emotion")
            result = {}
            for row in await cursor.fetchall():
                result[row["key"]] = round(self._apply_decay(row["value"], row["key"], row["updated_at"]), 1)
            return result

    FREQ_COOLDOWN = 300  # 빈도 감쇠 쿨다운 (5분)
    FREQ_MIN_MULT = 0.3  # 아무리 빨라도 최소 30% 반영

    TARGET_STD = 20  # 목표 표준편차 (넓은 분포 허용)

    def _adjust_delta(self, delta: float, axis: str, current_value: float,
                      last_interaction_str: str | None = None,
                      server_avg: float | None = None,
                      server_std: float | None = None) -> float:
        """보정된 변화량 계산."""
        delta = max(-self.AXIS_DELTA_MAX, min(self.AXIS_DELTA_MAX, float(delta)))

        # 1. 빈도 감쇠: 최근 대화일수록 효과 감소 (최소 30% 보장)
        if last_interaction_str:
            try:
                last = datetime.fromisoformat(last_interaction_str)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                freq_mult = min(1.0, elapsed / self.FREQ_COOLDOWN)
                freq_mult = max(self.FREQ_MIN_MULT, freq_mult)
                delta *= freq_mult
            except Exception:
                pass

        # 2. 분산 유지: 풀 분산이 목표보다 낮을 때만 증폭
        if server_std is not None and server_std > 0 and server_std < self.TARGET_STD:
            variance_mult = self.TARGET_STD / server_std
            variance_mult = min(2.0, variance_mult)
            delta *= variance_mult

        # 3. 서버 평균 정규화: 평균을 50으로 되돌리는 방향 약간 증폭
        if server_avg is not None:
            avg_dist = abs(server_avg - self.NEUTRAL) / self.NEUTRAL  # 0 ~ 1
            helps_avg = (server_avg > self.NEUTRAL and delta < 0) or \
                        (server_avg < self.NEUTRAL and delta > 0)
            if helps_avg:
                delta *= 1.0 + avg_dist * 0.3  # 최대 30% 증폭
            else:
                delta *= max(0.7, 1.0 - avg_dist * 0.3)  # 최대 30% 저항

        return max(-self.AXIS_DELTA_MAX, min(self.AXIS_DELTA_MAX, delta))

    ACTIVE_MINUTES = 10  # 활발할 때: 최근 N분
    ACTIVE_MIN_USERS = 10  # 한산할 때: 최소 N명

    async def _get_server_stats(self, db, axis: str) -> tuple[float | None, float | None]:
        """서버 평균 + 표준편차. 10분 내 유저가 10명 미만이면 최근 10명 기준."""
        if axis not in self.USER_AXES:
            return None, None
        import math
        # 먼저 최근 10분 내 유저 수 확인
        cursor = await db.execute(
            f"SELECT COUNT(*) as cnt, AVG({axis}) as avg FROM user_emotion WHERE last_interaction > datetime('now', ?)",
            (f"-{self.ACTIVE_MINUTES} minutes",))
        row = await cursor.fetchone()
        if row and row["cnt"] >= self.ACTIVE_MIN_USERS:
            avg = row["avg"]
            # 표준편차 계산
            cursor2 = await db.execute(
                f"SELECT {axis} FROM user_emotion WHERE last_interaction > datetime('now', ?)",
                (f"-{self.ACTIVE_MINUTES} minutes",))
            vals = [r[0] for r in await cursor2.fetchall()]
        else:
            cursor = await db.execute(
                f"SELECT {axis} FROM user_emotion ORDER BY last_interaction DESC LIMIT ?",
                (self.ACTIVE_MIN_USERS,))
            vals = [r[0] for r in await cursor.fetchall()]
            avg = sum(vals) / len(vals) if vals else None

        if not vals or len(vals) < 2:
            return avg, None
        mean = sum(vals) / len(vals)
        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
        return avg, math.sqrt(variance)

    async def update_emotion(self, changes: dict, target_user_id: str = None,
                             target_user_name: str = None, reason: str = None,
                             message_id: str = None) -> dict:
        """감정 변화 적용. user_ 축은 target 유저에, self_/server_ 축은 전역에."""
        import json
        result = {}

        # message_id 중복 체크
        if message_id and target_user_id:
            async with aiosqlite.connect(self.path) as check_db:
                cursor = await check_db.execute(
                    "SELECT 1 FROM emotion_log WHERE message_id = ? AND target = ?",
                    (message_id, target_user_id))
                if await cursor.fetchone():
                    logger.info(f"[감정] 중복 건너뜀: msg={message_id} target={target_user_id}")
                    return result

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            # user_ 축 처리
            user_changes = {k: v for k, v in changes.items() if k in self.USER_AXES}
            if user_changes and target_user_id:
                cursor = await db.execute(
                    "SELECT * FROM user_emotion WHERE user_id = ?", (target_user_id,))
                row = await cursor.fetchone()
                current = dict(row) if row else {"comfort": self.NEUTRAL, "affinity": self.NEUTRAL, "trust": self.NEUTRAL,
                                                  "interaction_count": 0}
                last_interaction = current.get("last_interaction")

                for axis, delta in user_changes.items():
                    cur_val = current.get(axis, self.NEUTRAL)
                    server_avg, server_std = await self._get_server_stats(db, axis)
                    adjusted = self._adjust_delta(delta, axis, cur_val, last_interaction, server_avg, server_std)
                    current[axis] = max(self.AXIS_RANGE[0], min(self.AXIS_RANGE[1], cur_val + adjusted))

                current["interaction_count"] = current.get("interaction_count", 0) + 1
                current["last_interaction"] = datetime.now(timezone.utc).isoformat()

                await db.execute("""
                    INSERT INTO user_emotion (user_id, user_name, comfort, affinity, trust, interaction_count, last_interaction)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        user_name=excluded.user_name,
                        comfort=excluded.comfort, affinity=excluded.affinity, trust=excluded.trust,
                        interaction_count=excluded.interaction_count,
                        last_interaction=excluded.last_interaction
                """, (target_user_id, target_user_name,
                      current["comfort"], current["affinity"], current["trust"],
                      current["interaction_count"], current["last_interaction"]))

                for axis in self.USER_AXES:
                    result[f"user_{axis}"] = current[axis]

            # self_/server_/needs 축 처리
            global_changes = {k: v for k, v in changes.items()
                              if k in self.SELF_AXES + self.SERVER_AXES + self.NEEDS_AXES}
            for axis, delta in global_changes.items():
                cursor = await db.execute(
                    "SELECT value, updated_at FROM bot_emotion WHERE key = ?", (axis,))
                row = await cursor.fetchone()
                old_val = row["value"] if row else self.NEUTRAL
                last_updated = row["updated_at"] if row else None
                adjusted = self._adjust_delta(delta, axis, old_val, last_updated)
                new_val = max(self.AXIS_RANGE[0], min(self.AXIS_RANGE[1], old_val + adjusted))
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
                "INSERT INTO emotion_log (target, user_name, changes, reason, message_id) VALUES (?, ?, ?, ?, ?)",
                (target_user_id or "self", target_user_name or "self", changes_str, reason, message_id))

            await db.commit()
            return result

    # ── Evaluation 피드백 ──────────────────────────────────

    async def save_feedback(self, user_id: str, feedback: str):
        """Evaluation 피드백 저장 (유저별 최신 1건)"""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO evaluation_feedback (user_id, feedback) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET feedback=excluded.feedback, created_at=datetime('now')
            """, (user_id, feedback))
            await db.commit()

    async def get_feedback(self, user_id: str) -> str | None:
        """유저별 최신 Evaluation 피드백 조회"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT feedback FROM evaluation_feedback WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return row[0] if row else None

    # ── 대화 요약 ──────────────────────────────────────────

    async def save_user_summary(self, user_id: str, summary: str):
        """유저별 대화 요약 저장 (최신 1건 덮어쓰기)"""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO user_summary (user_id, summary) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET summary=excluded.summary, updated_at=datetime('now')
            """, (user_id, summary))
            await db.commit()

    async def get_user_summary(self, user_id: str) -> str | None:
        """유저별 대화 요약 조회"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT summary FROM user_summary WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def save_channel_summary(self, channel_id: str, summary: str):
        """채널별 흐름 요약 저장 (최신 1건 덮어쓰기)"""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO channel_summary (channel_id, summary) VALUES (?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET summary=excluded.summary, updated_at=datetime('now')
            """, (channel_id, summary))
            await db.commit()

    async def get_channel_summary(self, channel_id: str) -> str | None:
        """채널별 흐름 요약 조회"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT summary FROM channel_summary WHERE channel_id = ?", (channel_id,))
            row = await cursor.fetchone()
            return row[0] if row else None

    # ── 메시지 로그 ──────────────────────────────────────

    async def save_message(self, message_id: str, channel_id: str,
                           author_id: str, author_name: str, content: str,
                           reference_id: str | None = None, is_bot: bool = False):
        """메시지 저장."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT OR IGNORE INTO message_log
                (message_id, channel_id, author_id, author_name, content, reference_id, is_bot, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (message_id, channel_id, author_id, author_name,
                  content[:2000], reference_id, 1 if is_bot else 0))
            await db.commit()

    async def get_messages_before(self, channel_id: str, message_id: str,
                                  limit: int = 10) -> list[dict]:
        """특정 메시지 직전 N건 조회."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM message_log
                WHERE channel_id = ? AND created_at < (
                    SELECT created_at FROM message_log WHERE message_id = ?
                )
                ORDER BY created_at DESC LIMIT ?
            """, (channel_id, message_id, limit))
            rows = [dict(r) for r in await cursor.fetchall()]
            rows.reverse()
            return rows

    async def get_messages_after(self, channel_id: str, message_id: str,
                                 limit: int = 5) -> list[dict]:
        """특정 메시지 직후 N건 조회."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM message_log
                WHERE channel_id = ? AND created_at > (
                    SELECT created_at FROM message_log WHERE message_id = ?
                )
                ORDER BY created_at ASC LIMIT ?
            """, (channel_id, message_id, limit))
            return [dict(r) for r in await cursor.fetchall()]

    async def get_messages_recent(self, channel_id: str, before_message_id: str,
                                   limit: int = 10) -> list[dict]:
        """현재 메시지 직전 N건 조회."""
        return await self.get_messages_before(channel_id, before_message_id, limit)

    async def get_reply_chain(self, message_id: str, limit: int = 5) -> list[dict]:
        """답글 체인을 reference_id로 역추적. 최근 N건."""
        chain = []
        current_id = message_id
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            for _ in range(limit):
                cursor = await db.execute(
                    "SELECT * FROM message_log WHERE message_id = ?", (current_id,))
                row = await cursor.fetchone()
                if not row:
                    break
                ref_id = row["reference_id"]
                if not ref_id:
                    break
                # 답글 대상 메시지 조회
                cursor2 = await db.execute(
                    "SELECT * FROM message_log WHERE message_id = ?", (ref_id,))
                ref_row = await cursor2.fetchone()
                if not ref_row:
                    break
                chain.append(dict(ref_row))
                current_id = ref_id
        chain.reverse()
        return chain

    async def cleanup_old_messages(self, days: int = 30):
        """오래된 메시지 정리."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM message_log WHERE created_at < datetime('now', ?)",
                (f"-{days} days",))
            await db.commit()

    # ── 선물 대기열 ─────────────────────────────────────

    async def save_pending_gift(self, channel_id: str, buyer_id: str,
                                 item_id: str, item_name: str, item_emoji: str,
                                 effects: str) -> int:
        """선물 대기열에 추가. 반환: gift ID"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("""
                INSERT INTO pending_gifts (channel_id, buyer_id, item_id, item_name, item_emoji, effects)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (channel_id, buyer_id, item_id, item_name, item_emoji, effects))
            await db.commit()
            return cursor.lastrowid

    async def pop_pending_gift(self, channel_id: str) -> dict | None:
        """채널의 가장 오래된 대기 선물을 꺼내고 삭제."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM pending_gifts WHERE channel_id = ? ORDER BY id LIMIT 1",
                (channel_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            gift = dict(row)
            await db.execute("DELETE FROM pending_gifts WHERE id = ?", (gift["id"],))
            await db.commit()
            return gift

    async def save_gift_log(self, buyer_id: str, buyer_name: str,
                            item_emoji: str, item_name: str, item_price: int,
                            message: str = None,
                            recipient_id: str = None, recipient_name: str = None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO gift_log (buyer_id, buyer_name, recipient_id, recipient_name, "
                "item_emoji, item_name, item_price, message) VALUES (?,?,?,?,?,?,?,?)",
                (buyer_id, buyer_name, recipient_id, recipient_name,
                 item_emoji, item_name, item_price, message))
            await db.commit()

    async def get_gift_log(self, limit: int = 5) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM gift_log ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in await cursor.fetchall()]

    async def get_gifts_for_prompt(self, user_id: str, bot_id: str, limit: int = 5) -> dict:
        """프롬프트용 선물 기록 4분류."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            # 1. 봇 자체 소비 (recipient 없음 = 자기가 사먹은 것)
            cursor = await db.execute(
                "SELECT * FROM gift_log WHERE buyer_id = ? AND recipient_id IS NULL ORDER BY id DESC LIMIT ?",
                (bot_id, limit))
            bot_self = [dict(r) for r in await cursor.fetchall()]
            # 2. 봇 → 상대유저
            cursor = await db.execute(
                "SELECT * FROM gift_log WHERE buyer_id = ? AND recipient_id = ? ORDER BY id DESC LIMIT ?",
                (bot_id, user_id, limit))
            bot_to_user = [dict(r) for r in await cursor.fetchall()]
            # 3. 상대유저 → 봇
            cursor = await db.execute(
                "SELECT * FROM gift_log WHERE buyer_id = ? AND (recipient_id IS NULL OR recipient_id = ?) ORDER BY id DESC LIMIT ?",
                (user_id, bot_id, limit))
            user_to_bot = [dict(r) for r in await cursor.fetchall()]
            # 4. 다른 유저 <-> 봇 (상대유저 제외, 자체 소비 제외)
            cursor = await db.execute(
                "SELECT * FROM gift_log WHERE "
                "((buyer_id != ? AND buyer_id != ? AND (recipient_id IS NULL OR recipient_id = ?)) "
                " OR (buyer_id = ? AND recipient_id IS NOT NULL AND recipient_id != ? AND recipient_id != ?)) "
                "ORDER BY id DESC LIMIT ?",
                (user_id, bot_id, bot_id,
                 bot_id, user_id, bot_id,
                 limit))
            others = [dict(r) for r in await cursor.fetchall()]
        return {
            "bot_self": bot_self,
            "bot_to_user": bot_to_user,
            "user_to_bot": user_to_bot,
            "others": others,
        }

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
