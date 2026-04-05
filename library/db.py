"""
도서관 DB (엔트리, 파일)
"""

import aiosqlite
import logging
from datetime import datetime, timezone
from config import LIBRARY_DB_PATH

logger = logging.getLogger("LibraryDB")


class LibraryDB:
    def __init__(self):
        self.path = LIBRARY_DB_PATH

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")

            await db.execute("""
                CREATE TABLE IF NOT EXISTS books (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_id    TEXT NOT NULL,
                    creator_name  TEXT NOT NULL,
                    title         TEXT NOT NULL,
                    alias         TEXT,
                    author        TEXT,
                    author_alias  TEXT,
                    description   TEXT,
                    created_at    TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id       INTEGER NOT NULL,
                    uploader_id   TEXT NOT NULL,
                    uploader_name TEXT NOT NULL,
                    title         TEXT NOT NULL,
                    description   TEXT NOT NULL,
                    filename      TEXT NOT NULL,
                    stored_name   TEXT NOT NULL UNIQUE,
                    file_size     INTEGER NOT NULL,
                    mime_type     TEXT,
                    uploaded_at   TEXT NOT NULL,
                    download_count INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(book_id) REFERENCES books(id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS pages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    title      TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            await db.execute("""
                INSERT OR IGNORE INTO meta (key, value)
                VALUES ('catalog_updated_at', datetime('now'))
            """)

            # 마이그레이션
            async def _add_column(table, column, coltype):
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                except Exception:
                    pass

            await _add_column("books", "alias", "TEXT")
            await _add_column("books", "author", "TEXT")
            await _add_column("books", "author_alias", "TEXT")
            await _add_column("books", "page_id", "INTEGER DEFAULT 0")
            await _add_column("books", "sort_order", "INTEGER DEFAULT 0")
            await _add_column("books", "hidden", "INTEGER DEFAULT 0")
            await _add_column("files", "hidden", "INTEGER DEFAULT 0")

            # ── 지갑 (경제 시스템) ─────────────────────────────
            await db.execute("""
                CREATE TABLE IF NOT EXISTS wallets (
                    user_id    TEXT PRIMARY KEY,
                    username   TEXT NOT NULL,
                    balance    INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT NOT NULL,
                    type       TEXT NOT NULL,
                    amount     INTEGER NOT NULL,
                    note       TEXT,
                    item_emoji TEXT,
                    item_name  TEXT,
                    item_price INTEGER,
                    created_at TEXT NOT NULL
                )
            """)

            await _add_column("transactions", "item_emoji", "TEXT")
            await _add_column("transactions", "item_name", "TEXT")
            await _add_column("transactions", "item_price", "TEXT")

            await db.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_hash TEXT UNIQUE NOT NULL,
                    user_id      TEXT NOT NULL,
                    amount       INTEGER NOT NULL,
                    bolt11       TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    created_at   TEXT NOT NULL,
                    paid_at      TEXT,
                    message_id   TEXT,
                    channel_id   TEXT,
                    buy_item_id  TEXT
                )
            """)

            await _add_column("invoices", "buy_item_id", "TEXT")
            # 봇→유저 선물 대기 (AI 사서봇이 기록, 라이브러리 봇이 알림 전송)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pending_bot_gifts (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id   TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    recipient_name TEXT NOT NULL,
                    bot_name     TEXT NOT NULL,
                    item_emoji   TEXT NOT NULL,
                    item_name    TEXT NOT NULL,
                    item_price   INTEGER NOT NULL,
                    message      TEXT,
                    created_at   TEXT NOT NULL
                )
            """)
            await db.commit()
            logger.info("도서관 DB 초기화 완료")

    # ── 카탈로그 메타 ─────────────────────────────────────

    async def touch_catalog(self, db=None):
        """카탈로그 변경 시각 업데이트. 기존 db 연결 재사용 가능."""
        if db:
            await db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('catalog_updated_at', datetime('now'))")
        else:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('catalog_updated_at', datetime('now'))")
                await db.commit()

    async def get_catalog_updated_at(self) -> str:
        """카탈로그 마지막 변경 시각 반환"""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT value FROM meta WHERE key = 'catalog_updated_at'")
            row = await cursor.fetchone()
            return row[0] if row else ""

    # ── 엔트리 ────────────────────────────────────────────

    async def create_book(self, creator_id: str, creator_name: str,
                          title: str, alias: str | None,
                          author: str | None, author_alias: str | None,
                          description: str | None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("""
                INSERT INTO books (creator_id, creator_name, title, alias,
                                   author, author_alias, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (creator_id, creator_name, title, alias, author, author_alias, description, now))
            await self.touch_catalog(db)
            await db.commit()
            return cursor.lastrowid

    async def get_book(self, book_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM books WHERE id = ?", (book_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_books(self, page: int = 1, per_page: int = 10) -> tuple[list[dict], int]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM books")
            total = (await cursor.fetchone())["cnt"]
            offset = (page - 1) * per_page
            cursor = await db.execute("""
                SELECT b.*, COUNT(f.id) as file_count,
                       COALESCE(SUM(f.file_size), 0) as total_size
                FROM books b
                LEFT JOIN files f ON f.book_id = b.id
                GROUP BY b.id
                ORDER BY CASE WHEN b.page_id = 0 THEN 9999 ELSE b.page_id END ASC,
                         CASE WHEN b.sort_order = 0 THEN 9999 ELSE b.sort_order END ASC,
                         b.created_at ASC
                LIMIT ? OFFSET ?
            """, (per_page, offset))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows], total

    async def list_all_books(self, include_hidden=False) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            where = "" if include_hidden else "WHERE (b.hidden IS NULL OR b.hidden = 0)"
            cursor = await db.execute(f"""
                SELECT b.*, COUNT(f.id) as file_count
                FROM books b
                LEFT JOIN files f ON f.book_id = b.id
                {where}
                GROUP BY b.id
                ORDER BY CASE WHEN b.page_id = 0 THEN 9999 ELSE b.page_id END ASC,
                         CASE WHEN b.sort_order = 0 THEN 9999 ELSE b.sort_order END ASC,
                         b.created_at ASC
            """)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def list_books_by_user(self, creator_id: str) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT b.*, COUNT(f.id) as file_count
                FROM books b
                LEFT JOIN files f ON f.book_id = b.id
                WHERE b.creator_id = ? AND (b.hidden IS NULL OR b.hidden = 0)
                GROUP BY b.id
                ORDER BY CASE WHEN b.page_id = 0 THEN 9999 ELSE b.page_id END ASC,
                         CASE WHEN b.sort_order = 0 THEN 9999 ELSE b.sort_order END ASC,
                         b.created_at ASC
                LIMIT 25
            """, (creator_id,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_book_alias(self, book_id: int, alias: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE books SET alias = ? WHERE id = ?", (alias, book_id))
            await self.touch_catalog(db)
            await db.commit()

    async def update_book(self, book_id: int, title: str, alias: str | None,
                          author: str | None, author_alias: str | None,
                          description: str | None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                UPDATE books SET title = ?, alias = ?, author = ?,
                       author_alias = ?, description = ?
                WHERE id = ?
            """, (title, alias, author, author_alias, description, book_id))
            await self.touch_catalog(db)
            await db.commit()

    async def delete_book(self, book_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("DELETE FROM books WHERE id = ?", (book_id,))
            await db.execute("DELETE FROM files WHERE book_id = ?", (book_id,))
            await self.touch_catalog(db)
            await db.commit()
            return cursor.rowcount > 0

    # ── 파일 ──────────────────────────────────────────────

    async def add_file(self, book_id: int, uploader_id: str, uploader_name: str,
                       title: str, description: str,
                       filename: str, stored_name: str, file_size: int,
                       mime_type: str | None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("""
                INSERT INTO files (book_id, uploader_id, uploader_name, title, description,
                                   filename, stored_name, file_size, mime_type, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (book_id, uploader_id, uploader_name, title, description,
                  filename, stored_name, file_size, mime_type, now))
            await self.touch_catalog(db)
            await db.commit()
            return cursor.lastrowid

    async def list_book_files(self, book_id: int, include_hidden=False) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            hidden_filter = "" if include_hidden else "AND (hidden IS NULL OR hidden = 0)"
            cursor = await db.execute(
                f"SELECT * FROM files WHERE book_id = ? {hidden_filter} ORDER BY uploaded_at DESC",
                (book_id,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_file(self, file_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM files WHERE id = ? AND (hidden IS NULL OR hidden = 0)", (file_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def increment_download(self, file_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE files SET download_count = download_count + 1 WHERE id = ?", (file_id,))
            await db.commit()

    async def list_files_by_user(self, uploader_id: str) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT f.*, b.title as book_title
                FROM files f
                JOIN books b ON b.id = f.book_id
                WHERE f.uploader_id = ?
                ORDER BY f.uploaded_at DESC
                LIMIT 25
            """, (uploader_id,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_file(self, file_id: int, title: str, description: str, filename: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                UPDATE files SET title = ?, description = ?, filename = ?
                WHERE id = ?
            """, (title, description, filename, file_id))
            await self.touch_catalog(db)
            await db.commit()

    async def delete_file(self, file_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
            await self.touch_catalog(db)
            await db.commit()
            return cursor.rowcount > 0

    # ── 검색 ──────────────────────────────────────────────

    async def search_books(self, keyword: str) -> list[dict]:
        like = f"%{keyword}%"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT b.*, COUNT(f.id) as file_count
                FROM books b
                LEFT JOIN files f ON f.book_id = b.id
                WHERE b.title LIKE ?
                   OR b.alias LIKE ?
                   OR b.author LIKE ?
                   OR b.author_alias LIKE ?
                   OR b.description LIKE ?
                GROUP BY b.id
                ORDER BY CASE WHEN b.page_id = 0 THEN 9999 ELSE b.page_id END ASC,
                         CASE WHEN b.sort_order = 0 THEN 9999 ELSE b.sort_order END ASC,
                         b.created_at ASC
                LIMIT 10
            """, (like, like, like, like, like))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_book_detail(self, book_id: int) -> dict | None:
        book = await self.get_book(book_id)
        if not book:
            return None
        files = await self.list_book_files(book_id)
        book["files"] = files
        return book

    # ── 페이지 ────────────────────────────────────────────

    async def create_page(self, title: str, sort_order: int = 0) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO pages (title, sort_order) VALUES (?, ?)",
                (title, sort_order))
            await db.commit()
            return cursor.lastrowid

    async def set_hidden(self, book_id: int, hidden: bool):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE books SET hidden = ? WHERE id = ?", (1 if hidden else 0, book_id))
            await self.touch_catalog(db)
            await db.commit()

    async def set_file_hidden(self, file_id: int, hidden: bool):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE files SET hidden = ? WHERE id = ?", (1 if hidden else 0, file_id))
            await self.touch_catalog(db)
            await db.commit()

    async def unassign_page_books(self, page_id: int):
        """페이지의 엔트리들을 미배정(0)으로"""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE books SET page_id = 0 WHERE page_id = ?", (page_id,))
            await self.touch_catalog(db)
            await db.commit()

    async def set_page_hidden(self, page_id: int, hidden: bool):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE pages SET hidden = ? WHERE id = ?", (1 if hidden else 0, page_id))
            await self.touch_catalog(db)
            await db.commit()

    async def list_pages(self, include_hidden=False) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            where = "" if include_hidden else "WHERE (hidden IS NULL OR hidden = 0)"
            cursor = await db.execute(f"""
                SELECT * FROM pages {where}
                ORDER BY CASE WHEN sort_order = 0 THEN 9999 ELSE sort_order END ASC, id ASC
            """)
            return [dict(r) for r in await cursor.fetchall()]

    async def get_page(self, page_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM pages WHERE id = ?", (page_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_page(self, page_id: int, title: str, sort_order: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE pages SET title = ?, sort_order = ? WHERE id = ?",
                (title, sort_order, page_id))
            await db.commit()

    async def delete_page(self, page_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE books SET page_id = 0 WHERE page_id = ?", (page_id,))
            await db.execute("DELETE FROM pages WHERE id = ?", (page_id,))
            await self.touch_catalog(db)
            await db.commit()

    async def assign_book_page(self, book_id: int, page_id: int, sort_order: int = 0):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE books SET page_id = ?, sort_order = ? WHERE id = ?",
                (page_id, sort_order, book_id))
            await self.touch_catalog(db)
            await db.commit()

    # ── 지갑 ──────────────────────────────────────────────

    async def get_or_create_wallet(self, user_id: str, username: str) -> dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM wallets WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            if row:
                await db.execute(
                    "UPDATE wallets SET username = ? WHERE user_id = ?",
                    (username, user_id))
                await db.commit()
                return dict(row)
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "INSERT INTO wallets (user_id, username, balance, created_at) VALUES (?,?,0,?)",
                (user_id, username, now))
            await db.commit()
            return {"user_id": user_id, "username": username, "balance": 0, "created_at": now}

    async def get_balance(self, user_id: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_wallet_id_by_name(self, username: str) -> str | None:
        """username으로 지갑 user_id 조회."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT user_id FROM wallets WHERE username = ?", (username,))
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_balance(self, user_id: str, username: str, amount: int):
        """잔고를 지정한 값으로 설정."""
        await self.get_or_create_wallet(user_id, username)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE wallets SET balance = ? WHERE user_id = ?", (amount, user_id))
            await db.commit()

    async def charge_balance(self, user_id: str, username: str, amount: int) -> int:
        """잔고 충전. 새 잔고 반환."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN EXCLUSIVE")
            try:
                # 지갑 없으면 생성
                cursor = await db.execute(
                    "SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
                row = await cursor.fetchone()
                if row is None:
                    now = datetime.now(timezone.utc).isoformat()
                    await db.execute(
                        "INSERT INTO wallets (user_id, username, balance, created_at) VALUES (?,?,0,?)",
                        (user_id, username, now))
                await db.execute(
                    "UPDATE wallets SET balance = balance + ?, username = ? WHERE user_id = ?",
                    (amount, username, user_id))
                await db.execute(
                    "INSERT INTO transactions (user_id, type, amount, note, created_at) VALUES (?,?,?,?,?)",
                    (user_id, "charge", amount, "충전", datetime.now(timezone.utc).isoformat()))
                await db.commit()
            except Exception:
                await db.execute("ROLLBACK")
                raise
            cursor = await db.execute(
                "SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return row[0]

    async def spend_balance(self, user_id: str, amount: int, note: str = "",
                            item_emoji: str = None, item_name: str = None,
                            item_price: int = None) -> int | None:
        """잔고 차감. 잔고 부족 시 None, 성공 시 새 잔고 반환."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN EXCLUSIVE")
            try:
                cursor = await db.execute(
                    "SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
                row = await cursor.fetchone()
                if row is None or row[0] < amount:
                    await db.execute("ROLLBACK")
                    return None
                await db.execute(
                    "UPDATE wallets SET balance = balance - ? WHERE user_id = ?",
                    (amount, user_id))
                await db.execute(
                    "INSERT INTO transactions (user_id, type, amount, note, item_emoji, item_name, item_price, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (user_id, "buy", amount, note, item_emoji, item_name, item_price,
                     datetime.now(timezone.utc).isoformat()))
                await db.commit()
            except Exception:
                await db.execute("ROLLBACK")
                raise
            cursor = await db.execute(
                "SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return row[0]

    # ── 인보이스 ──────────────────────────────────────────

    async def save_invoice(self, payment_hash: str, user_id: str, amount: int,
                           bolt11: str, message_id: str = None, channel_id: str = None,
                           buy_item_id: str = None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO invoices (payment_hash, user_id, amount, bolt11, status, created_at, message_id, channel_id, buy_item_id) "
                "VALUES (?,?,?,?,'pending',?,?,?,?)",
                (payment_hash, user_id, amount, bolt11,
                 datetime.now(timezone.utc).isoformat(), message_id, channel_id, buy_item_id))
            await db.commit()

    async def mark_invoice_paid(self, payment_hash: str) -> dict | None:
        """인보이스 paid 처리 + 잔고 추가. 이중처리 방지."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN EXCLUSIVE")
            try:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM invoices WHERE payment_hash = ? AND status = 'pending'",
                    (payment_hash,))
                inv = await cursor.fetchone()
                if not inv:
                    await db.execute("ROLLBACK")
                    return None
                inv = dict(inv)
                now = datetime.now(timezone.utc).isoformat()
                cur2 = await db.execute(
                    "UPDATE invoices SET status='paid', paid_at=? WHERE payment_hash=? AND status='pending'",
                    (now, payment_hash))
                if cur2.rowcount == 0:
                    await db.execute("ROLLBACK")
                    return None
                # 잔고 추가
                cursor = await db.execute(
                    "SELECT balance FROM wallets WHERE user_id = ?", (inv["user_id"],))
                row = await cursor.fetchone()
                if row is None:
                    await db.execute(
                        "INSERT INTO wallets (user_id, username, balance, created_at) VALUES (?,?,0,?)",
                        (inv["user_id"], "", now))
                await db.execute(
                    "UPDATE wallets SET balance = balance + ? WHERE user_id = ?",
                    (inv["amount"], inv["user_id"]))
                await db.execute(
                    "INSERT INTO transactions (user_id, type, amount, note, created_at) VALUES (?,?,?,?,?)",
                    (inv["user_id"], "charge", inv["amount"], "Lightning 충전", now))
                await db.commit()
            except Exception:
                await db.execute("ROLLBACK")
                raise
            return inv

    async def get_pending_invoices(self, expire_seconds: int = 3600) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            # 만료된 것 정리
            await db.execute(
                "UPDATE invoices SET status='expired' WHERE status='pending' "
                "AND datetime(created_at) <= datetime('now', ? || ' seconds')",
                (f"-{expire_seconds}",))
            await db.commit()
            cursor = await db.execute(
                "SELECT * FROM invoices WHERE status = 'pending'")
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def cancel_user_pending_invoices(self, user_id: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE invoices SET status='cancelled' WHERE user_id = ? AND status = 'pending'",
                (user_id,))
            await db.commit()

    async def cancel_invoice(self, user_id: str, payment_hash: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE invoices SET status='cancelled' WHERE user_id = ? AND payment_hash = ? AND status = 'pending'",
                (user_id, payment_hash))
            await db.commit()
            return cursor.rowcount > 0

    async def get_gift_history(self, user_id: str, limit: int = 5, offset: int = 0) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT item_emoji, item_name, item_price, created_at FROM transactions "
                "WHERE user_id = ? AND type = 'buy' ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_gift_count(self, user_id: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM transactions WHERE user_id = ? AND type = 'buy'",
                (user_id,))
            row = await cursor.fetchone()
            return row[0]

    async def get_total_gifted(self, user_id: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id = ? AND type = 'buy'",
                (user_id,))
            row = await cursor.fetchone()
            return row[0]
