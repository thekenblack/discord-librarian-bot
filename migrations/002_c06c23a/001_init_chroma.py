"""
ChromaDB 벡터 스토어 초기화 — 기존 SQLite 데이터를 ChromaDB로 동기화
봇 시작 시에도 자동 동기화되지만, 마이그레이션에서 미리 실행하면 첫 시작이 빠르다.
"""
import sqlite3
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8") as f:
    conf = json.load(f)

data_dir = os.path.join(BASE_DIR, conf["paths"]["data_dir"])
db_path = os.path.join(data_dir, conf["db"]["librarian"])
chroma_path = os.path.join(data_dir, "chroma")

try:
    import chromadb
except ImportError:
    print("chromadb 미설치 — 건너뜀 (봇 시작 시 자동 동기화)")
    exit(0)

if not os.path.exists(db_path):
    print("librarian.db 없음 — 건너뜀")
    exit(0)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
client = chromadb.PersistentClient(path=chroma_path)

# ── knowledge ──
col = client.get_or_create_collection("knowledge", metadata={"hnsw:space": "cosine"})
if col.count() == 0:
    rows = conn.execute("SELECT id, category, content, priority FROM knowledge_base").fetchall()
    if rows:
        col.add(
            ids=[f"k_{r['id']}" for r in rows],
            documents=[r["content"] for r in rows],
            metadatas=[{"category": r["category"] or "",
                        "priority": r["priority"] or 50} for r in rows],
        )
        print(f"knowledge: {len(rows)}건 동기화")

# ── learned (forgotten 제외) ──
col = client.get_or_create_collection("learned", metadata={"hnsw:space": "cosine"})
if col.count() == 0:
    rows = conn.execute(
        "SELECT id, content, author FROM learned "
        "WHERE forgotten IS NULL OR forgotten = 0"
    ).fetchall()
    if rows:
        col.add(
            ids=[f"l_{r['id']}" for r in rows],
            documents=[r["content"] for r in rows],
            metadatas=[{"author": r["author"] or ""} for r in rows],
        )
        print(f"learned: {len(rows)}건 동기화")

# ── customs ──
col = client.get_or_create_collection("customs", metadata={"hnsw:space": "cosine"})
if col.count() == 0:
    rows = conn.execute("SELECT id, category, content FROM customs").fetchall()
    if rows:
        col.add(
            ids=[f"c_{r['id']}" for r in rows],
            documents=[r["content"] for r in rows],
            metadatas=[{"category": r["category"] or ""} for r in rows],
        )
        print(f"customs: {len(rows)}건 동기화")

# ── book_knowledge (done만) ──
col = client.get_or_create_collection("book_knowledge", metadata={"hnsw:space": "cosine"})
if col.count() == 0:
    rows = conn.execute(
        "SELECT id, source, content FROM book_knowledge WHERE status = 'done'"
    ).fetchall()
    if rows:
        col.add(
            ids=[f"b_{r['id']}" for r in rows],
            documents=[r["content"] for r in rows],
            metadatas=[{"source": r["source"] or ""} for r in rows],
        )
        print(f"book_knowledge: {len(rows)}건 동기화")

conn.close()
print("ChromaDB 초기 동기화 완료")
