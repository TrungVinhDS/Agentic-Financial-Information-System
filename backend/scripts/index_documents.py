"""
Index PDF trong data/documents vào ChromaDB (tự chạy mỗi lần container backend khởi động).

    python scripts/index_documents.py           # chỉ file mới / đã thay đổi
    python scripts/index_documents.py --force   # index lại toàn bộ
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nodes.rag_retriever import index_documents

if __name__ == "__main__":
    index_documents(force_reindex="--force" in sys.argv)
