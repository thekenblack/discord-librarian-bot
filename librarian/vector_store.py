"""
벡터 검색 (ChromaDB)
SQLite가 원본, ChromaDB는 검색 인덱스.
"""

import chromadb
import logging

logger = logging.getLogger("VectorStore")

COLLECTIONS = ("knowledge", "learned", "customs", "book_knowledge")


class VectorStore:
    def __init__(self, path: str):
        self.client = chromadb.PersistentClient(path=path)
        self._cols = {}
        for name in COLLECTIONS:
            self._cols[name] = self.client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )

    def _col(self, name: str):
        return self._cols[name]

    # ── 단건 ──────────────────────────────────────────

    def add(self, collection: str, doc_id: str, text: str,
            metadata: dict | None = None):
        if not text or not text.strip():
            return
        kwargs = {"ids": [doc_id], "documents": [text]}
        if metadata:
            kwargs["metadatas"] = [metadata]
        self._col(collection).upsert(**kwargs)

    def remove(self, collection: str, doc_id: str):
        try:
            self._col(collection).delete(ids=[doc_id])
        except Exception:
            pass

    # ── 배치 ──────────────────────────────────────────

    def add_batch(self, collection: str, ids: list[str],
                  documents: list[str],
                  metadatas: list[dict] | None = None):
        if not ids:
            return
        valid = []
        for i, (did, doc) in enumerate(zip(ids, documents)):
            if doc and doc.strip():
                meta = metadatas[i] if metadatas else None
                valid.append((did, doc, meta))
        if not valid:
            return
        v_ids, v_docs, v_metas = zip(*valid)
        kwargs = {"ids": list(v_ids), "documents": list(v_docs)}
        if metadatas:
            kwargs["metadatas"] = list(v_metas)
        self._col(collection).upsert(**kwargs)

    # ── 검색 ──────────────────────────────────────────

    def search(self, collection: str, query: str,
               n_results: int = 3) -> list[dict]:
        col = self._col(collection)
        count = col.count()
        if count == 0:
            return []
        try:
            results = col.query(
                query_texts=[query],
                n_results=min(n_results, count),
            )
        except Exception as e:
            logger.warning(f"벡터 검색 실패 ({collection}): {e}")
            return []

        items = []
        for i in range(len(results["ids"][0])):
            items.append({
                "id": results["ids"][0][i],
                "document": results["documents"][0][i],
                "distance": results["distances"][0][i] if results.get("distances") else None,
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else None,
            })
        return items

    # ── 유틸 ──────────────────────────────────────────

    def count(self, collection: str) -> int:
        return self._col(collection).count()

    def reset(self, collection: str):
        self.client.delete_collection(collection)
        self._cols[collection] = self.client.get_or_create_collection(
            name=collection,
            metadata={"hnsw:space": "cosine"},
        )
