# Build context = thư mục gốc repo (xem docker-compose.yml)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf

WORKDIR /app

COPY requirements.txt .
# torch bản CPU (nhỏ hơn bản CUDA mặc định vài GB), rồi mới tới phần còn lại
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install -r requirements.txt

# Tải sẵn embedding model vào image để câu hỏi RAG đầu tiên không phải chờ download
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
# Model đã nằm trong image: chạy offline, không gọi HuggingFace lúc khởi động
ENV HF_HUB_OFFLINE=1     TRANSFORMERS_OFFLINE=1

COPY backend/ .

EXPOSE 8000

# migrate bảng chat -> import CSV vào Postgres -> index PDF (bỏ qua file đã index) -> chạy server
CMD ["sh", "-c", "python manage.py migrate --noinput && python db/init_db.py && python scripts/index_documents.py && python manage.py runserver 0.0.0.0:8000"]
