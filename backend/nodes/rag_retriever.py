"""
RAG Retriever Node - Tìm context trong PDF cho câu hỏi kiến thức tổng quát.

1. index_documents(): đọc PDF trong data/documents, cắt chunk, lưu vào ChromaDB
2. rag_retrieve(): index (nếu có file mới) rồi semantic search các chunk liên quan tới câu hỏi
"""

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

DATA_DIR = Path(__file__).parent.parent / "data"
DOCUMENTS_DIR = DATA_DIR / "documents"
CHROMA_PERSIST_DIR = DATA_DIR / "chroma_db"
COLLECTION_NAME = "djia_documents"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
MIN_RELEVANCE = 0.3  # chunk có điểm thấp hơn coi như không liên quan


@lru_cache(maxsize=1)
def get_collection():
    """Collection ChromaDB (cosine) + embedding all-MiniLM-L6-v2. Cache để model chỉ load một lần."""
    client = chromadb.PersistentClient(
        path=str(CHROMA_PERSIST_DIR), settings=Settings(anonymized_telemetry=False)
    )
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        ),
        metadata={"hnsw:space": "cosine"},
    )


def index_documents(force_reindex: bool = False) -> int:
    """Index các PDF mới/đã thay đổi (so bằng MD5). Trả về số file vừa index."""
    collection = get_collection()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    indexed = 0
    for pdf_path in sorted(DOCUMENTS_DIR.glob("*.pdf")):
        file_hash = hashlib.md5(pdf_path.read_bytes()).hexdigest()
        existing = collection.get(where={"source": pdf_path.name}, limit=1)
        if existing["ids"]:
            if not force_reindex and existing["metadatas"][0].get("file_hash") == file_hash:
                print(f"Skipping {pdf_path.name} (already indexed)")
                continue
            collection.delete(where={"source": pdf_path.name})

        pages = (page.extract_text() for page in PdfReader(str(pdf_path)).pages)
        chunks = splitter.split_text("\n\n".join(p for p in pages if p))
        if not chunks:
            print(f"Warning: no text extracted from {pdf_path.name}")
            continue

        for start in range(0, len(chunks), 100):  # batch để không tốn RAM
            batch = range(start, min(start + 100, len(chunks)))
            collection.add(
                ids=[f"{pdf_path.stem}_{file_hash[:8]}_{i}" for i in batch],
                documents=[chunks[i] for i in batch],
                metadatas=[
                    {
                        "source": pdf_path.name,
                        "file_hash": file_hash,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                    }
                    for i in batch
                ],
            )
        indexed += 1
        print(f"Indexed {len(chunks)} chunks from {pdf_path.name}")

    print(f"Indexed {indexed} documents, collection size: {collection.count()}")
    return indexed


def retrieve_from_db(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Trả về top_k chunk gần nghĩa nhất với query (rỗng nếu chưa index hoặc lỗi)."""
    try:
        results = get_collection().query(
            query_texts=[query],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        print(f"Error retrieving from ChromaDB: {e}")
        return []
    return [
        {
            "content": doc,
            "source": meta.get("source", "unknown"),
            "chunk_index": meta.get("chunk_index", 0),
            "relevance_score": 1 - distance,  # cosine distance -> similarity
        }
        for doc, meta, distance in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        )
    ]


def rag_retrieve(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph Node: tìm context trong PDF cho câu hỏi KHÔNG liên quan SQL.

    Trả về state với has_rag_context (bool) và rag_context (các chunk đủ liên quan).
    """
    # Đảm bảo documents đã được index (file đã index và không đổi thì bỏ qua)
    index_documents()

    docs = [
        d
        for d in retrieve_from_db(state.get("question", ""), top_k=5)
        if d["relevance_score"] >= MIN_RELEVANCE
    ]
    return {**state, "has_rag_context": bool(docs), "rag_context": docs}
