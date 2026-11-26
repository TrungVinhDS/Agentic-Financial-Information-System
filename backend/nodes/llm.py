"""Một chỗ duy nhất gọi LLM: key + model lấy từ biến môi trường (docker-compose nạp từ .env).

Gọi qua API chuẩn OpenAI (mặc định Groq). Đổi sang OpenRouter / Cerebras / Ollama chỉ cần sửa LLM_BASE_URL + LLM_MODEL.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

LLM_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b")


def _openai_compat(prompt: str, retries: int = 3) -> str:
    """Thử lần lượt các model trong LLM_MODEL (phân cách dấu phẩy).

    Groq tính quota TPM riêng từng model, nên model này bị 429 thì đổi model khác ngay.
    Tất cả đều 429 thì chờ retry-after ngắn nhất (giây) rồi thử lại; phải chờ quá 30s (thường là hết quota ngày) thì raise.
    """
    waits = []
    for model in LLM_MODEL.split(","):
        try:
            return _post(prompt, model.strip())
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            waits.append(_retry_seconds(e.headers))
    if retries == 0 or min(waits) > 30:
        raise RuntimeError(f"Tất cả model {LLM_MODEL} đều bị rate limit (retry-after {min(waits)}s)")
    time.sleep(min(waits))
    return _openai_compat(prompt, retries - 1)


def _retry_seconds(headers) -> float:
    """Số giây cần chờ: retry-after, thiếu thì đọc x-ratelimit-reset-tokens (dạng "34.8s", "1m2s", "450ms")."""
    if headers.get("retry-after"):
        return float(headers["retry-after"])
    reset = headers.get("x-ratelimit-reset-tokens") or ""
    units = {"h": 3600, "m": 60, "s": 1, "ms": 0.001}
    return sum(float(n) * units[u] for n, u in re.findall(r"([\d.]+)(ms|h|m|s)", reset)) or 5.0


def _post(prompt: str, model: str) -> str:
    req = urllib.request.Request(
        f"{LLM_URL}/chat/completions",
        # temperature thấp: SQL/phân loại ổn định giữa các lần chạy
        data=json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}).encode(),
        # Groq chặn User-Agent mặc định của urllib (403), nên đặt tên client chung chung
        headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json", "User-Agent": "afis/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        text = json.load(r)["choices"][0]["message"]["content"] or ""
    # qwen3 có thể trả kèm <think>...</think>: bỏ đi để các node parse "SQL"/"FOUND: n"/SQL không bị lẫn
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def ask(prompt: str) -> str:
    """Gửi prompt, trả về text đã strip. Raise nếu thiếu key hoặc API lỗi (mỗi node tự có phương án dự phòng)."""
    if not LLM_KEY:
        raise RuntimeError("Thiếu LLM_API_KEY (điền vào file .env rồi restart backend)")
    try:
        return _openai_compat(prompt)
    except Exception as e:
        detail = e.read()[:300] if hasattr(e, "read") else ""
        print(f"LLM ({LLM_MODEL}) lỗi: {e} {detail}")
        raise


if __name__ == "__main__":
    assert _retry_seconds({"retry-after": "13"}) == 13
    assert abs(_retry_seconds({"x-ratelimit-reset-tokens": "1m2.5s"}) - 62.5) < 1e-9
    assert abs(_retry_seconds({"x-ratelimit-reset-tokens": "450ms"}) - 0.45) < 1e-9
    assert _retry_seconds({}) == 5.0
    print("ok")
