from typing import Dict, Any, Optional, List
import re

from config import SQL_SAMPLES_FILE
from nodes.llm import ask
from nodes.utils import normalize_text, extract_ticker


def load_sql_samples() -> List[str]:
    """Load SQL samples từ file."""
    if not SQL_SAMPLES_FILE.exists():
        return []
    text = SQL_SAMPLES_FILE.read_text(encoding="utf-8")
    return [s.strip() + ";" for s in re.split(r";\s*\n", text) if s.strip()]


def _top_samples(question: str, samples: List[str], k: int = 12) -> List[str]:
    """Lọc k mẫu có nhiều từ trùng với câu hỏi nhất (so với dòng comment MẪU CÂU HỎI / EN).

    Gửi cả ~100 mẫu (~13K token) vượt giới hạn 8K TPM của Groq free, nên lọc trước rồi mới hỏi LLM.
    ponytail: đếm từ trùng đơn giản; đổi sang embedding nếu lọc sót mẫu đúng.
    """
    words = lambda s: set(re.findall(r"\w+", normalize_text(s)))
    q = words(question)
    comments = lambda s: " ".join(l for l in s.splitlines() if l.strip().startswith("--"))
    return sorted(samples, key=lambda s: len(q & words(comments(s))), reverse=True)[:k]


def match_sample(question: str) -> Optional[str]:
    """Dùng LLM để chọn SQL sample phù hợp. Không có (hoặc LLM lỗi) thì trả về None."""
    sql_samples = _top_samples(question, load_sql_samples())
    if not sql_samples:
        return None

    samples_text = "\n".join([f"{i+1}. {sql}" for i, sql in enumerate(sql_samples)])

    prompt = f"""
Bạn là chuyên gia SQL của hệ thống Agentic Financial Information. Nhiệm vụ: từ danh sách SQL mẫu dưới đây, chọn CHÍNH XÁC một câu lệnh phù hợp nhất với câu hỏi.

CÂU HỎI: {question}

DANH SÁCH SQL MẪU (phải đọc toàn bộ trước khi quyết định):
{samples_text}

YÊU CẦU BẮT BUỘC:
- Chỉ chọn SQL nếu cấu trúc, bộ field SELECT, điều kiện WHERE, parameters (:ticker, :date, ...) hoàn toàn khớp với câu hỏi.
- Nếu câu hỏi cần 2 công ty, SQL phải có :ticker_a/:ticker_b hoặc logic tương ứng.
- Nếu cần theo ngày, SQL phải dùng date = CAST(:date AS DATE); theo năm dùng TO_CHAR(date,'YYYY') = :year.
- Tuyệt đối không chọn SQL chỉ "gần giống".

CÁCH TRẢ LỜI:
- Nếu tìm được, trả lời: FOUND: [index]; index bắt đầu từ 1 theo danh sách trên.
- Nếu không có SQL nào phù hợp 100%, trả lời: NO_MATCH.

Chỉ trả lời: FOUND: n hoặc NO_MATCH."""

    try:
        m = re.match(r"FOUND:\s*(\d+)", ask(prompt))
    except Exception as e:
        print(f"LLM validation error: {e}")
        return None
    idx = int(m.group(1)) - 1 if m else -1
    return sql_samples[idx] if 0 <= idx < len(sql_samples) else None


def match_sql_template(state: Dict[str, Any]) -> Dict[str, Any]:
    """Trích xuất ticker và tìm SQL template phù hợp với câu hỏi."""
    question = state.get("question", "")
    ticker = extract_ticker(question)

    # Câu hỏi vẽ biểu đồ: luôn để LLM sinh SQL (template chỉ trả về 1 giá trị, không đủ để vẽ)
    if state.get("needs_chart"):
        return {**state, "ticker": ticker, "sql": None, "used_sample": False}

    # Câu hỏi về tất cả công ty: template đều theo :ticker nên cũng để LLM sinh SQL
    q = normalize_text(question)
    is_all_companies = state.get("complexity", {}).get(
        "involves_multiple_companies", False
    ) or any(
        phrase in q
        for phrase in [
            "all companies",
            "all djia",
            "each company",
            "each djia company",
            "tất cả công ty",
            "mỗi công ty",
            "for all djia",
            "for each djia",
        ]
    )
    if is_all_companies:
        return {**state, "ticker": None, "sql": None, "used_sample": False}

    sql = match_sample(question)
    return {**state, "ticker": ticker, "sql": sql, "used_sample": sql is not None}
