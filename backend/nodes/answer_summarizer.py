from typing import Dict, Any, List
from datetime import date

import pandas as pd

from nodes.llm import ask

VIETNAMESE_CHARS = set(
    "àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ"
)


def _language_instruction(question: str) -> str:
    is_vietnamese = any(char in VIETNAMESE_CHARS for char in question.lower())
    return "Trả lời bằng tiếng Việt." if is_vietnamese else "Answer in English."


def _normalize_value(val: Any) -> Any:
    # pd.Timestamp và datetime đều là subclass của date
    return val.strftime("%Y-%m-%d") if isinstance(val, date) else val


def _derive_answer_fallback(df: pd.DataFrame) -> str:
    """Câu trả lời thô từ dòng đầu tiên, dùng khi LLM không khả dụng."""
    if df is None or df.empty:
        return "Không có dữ liệu phù hợp."
    row = {k: _normalize_value(v) for k, v in df.iloc[0].items()}
    if len(row) == 1:
        return str(next(iter(row.values())))
    for col in [
        "close",
        "open",
        "high",
        "low",
        "volume",
        "max_close",
        "min_close",
        "avg_close",
        "median_close",
        "a_close",
        "b_close",
    ]:
        if col in row:
            return str(row[col])
    return str(row)


def _summarize_with_llm(question: str, df: pd.DataFrame, sql: str = None) -> str:
    df = df if df is not None else pd.DataFrame()
    # Nhiều dòng: lấy 15 đầu + 10 cuối để LLM thấy đủ khoảng thời gian (trước đây chỉ head(25) nên báo sai mốc cuối)
    preview = (df if len(df) <= 25 else pd.concat([df.head(15), df.tail(10)])).map(_normalize_value)
    summary_input = preview.to_json(orient="records", force_ascii=False, indent=2)

    system_prompt = (
        "Bạn là trợ lý phân tích tài chính. Hãy đọc câu hỏi và kết quả truy vấn SQL, "
        f"Nếu {question} là tiếng Anh, hãy LUÔN LUÔN trả lời bằng tiếng Anh. Nếu {question} là tiếng Việt, hãy LUÔN LUÔN trả lời bằng tiếng Việt. "
        "Trả lời ngắn gọn, rõ ràng (làm tròn số nếu cần) và nêu tên công ty/mốc thời gian chính xác. "
        "Nếu dữ liệu rỗng, hãy nói rằng không tìm thấy dữ liệu phù hợp. "
        "Khi con số là âm, hãy mô tả bằng lời (ví dụ: '-62%' -> 'giảm 62%', '-74.76' USD -> 'giảm 74.76 USD'). "
        "Khi con số là dương, dùng phrasing 'tăng ...' hoặc 'là ...' tùy ngữ cảnh. "
        "Tránh lặp lại dấu âm trong câu trả lời; hãy diễn đạt theo nghĩa tăng/giảm để người đọc dễ hiểu."
    )

    prompt = (
        f"{system_prompt}\n\n"
        f"Câu hỏi: {question}\n"
        f"SQL đã chạy: {sql or 'N/A'}\n"
        f"Số dòng kết quả: {len(df)}\n"
        f"Dữ liệu ({'toàn bộ' if len(df) <= 25 else '15 dòng đầu + 10 dòng cuối'}):\n{summary_input}\n\n"
        "Trả lời:"
    )
    return ask(prompt) or _derive_answer_fallback(df)


def _answer_general_question_with_llm(question: str) -> str:
    """Trả lời câu hỏi chung (không SQL, không có context PDF) bằng LLM."""
    system_prompt = (
        "Bạn là trợ lý AI thông minh và hữu ích. "
        "Hãy trả lời câu hỏi một cách chính xác, rõ ràng và hữu ích. "
        f"{_language_instruction(question)}"
    )
    return ask(f"{system_prompt}\n\nCâu hỏi: {question}\n\nTrả lời:")


def _answer_with_rag_context(question: str, rag_context: List[Dict[str, Any]]) -> str:
    """Trả lời câu hỏi bằng LLM với context là các chunk PDF lấy từ RAG."""
    context_text = "\n\n---\n\n".join(
        f"[Document {i}] (Source: {doc.get('source', 'unknown')}, "
        f"Relevance: {doc.get('relevance_score', 0):.2f})\n{doc.get('content', '')}"
        for i, doc in enumerate(rag_context, 1)
    )

    system_prompt = f"""Bạn là trợ lý AI chuyên về thị trường chứng khoán và DJIA (Dow Jones Industrial Average).

RETRIEVED CONTEXT FROM PDF DOCUMENTS:
{context_text}

YÊU CẦU:
- Trả lời dựa trên context được cung cấp từ PDF documents
- Nếu context không đủ thông tin, hãy nói rõ và cung cấp kiến thức chung dựa trên context có sẵn
- {_language_instruction(question)}
- Giữ câu trả lời ngắn gọn, rõ ràng, dễ hiểu
- Cite source documents khi có thể (ví dụ: "Theo tài liệu X...")
- Nếu câu hỏi về giá cụ thể của một cổ phiếu tại thời điểm nào đó, hãy hướng dẫn người dùng hỏi lại với format phù hợp"""

    return ask(f"{system_prompt}\n\nCâu hỏi: {question}\n\nTrả lời:")


def summarize_answer(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph Node: Answer Summarizer - Tạo câu trả lời tự nhiên.

    Xử lý 3 trường hợp:
    1. Câu hỏi Other + có RAG context: Trả lời dựa trên PDF documents
    2. Câu hỏi Other + không có RAG context: Giao cho LLM trả lời
    3. Câu hỏi SQL-related: Trả lời dựa trên kết quả SQL
    """
    question = state.get("question", "")

    if not state.get("is_sql_related", True):
        rag_context = state.get("rag_context") or []
        try:
            answer = (
                _answer_with_rag_context(question, rag_context)
                if rag_context
                else _answer_general_question_with_llm(question)
            )
        except Exception as e:
            answer = f"Xin lỗi, đã xảy ra lỗi khi xử lý câu hỏi: {e}"
        return {**state, "answer": answer or "Xin lỗi, tôi không thể tạo câu trả lời cho câu hỏi này."}

    if state.get("error"):
        return {**state, "answer": f"Không thể thực thi truy vấn do lỗi: {state['error']}"}

    df = state.get("df")
    try:
        answer = _summarize_with_llm(question, df, state.get("sql"))
    except Exception as e:
        answer = _derive_answer_fallback(df)
        # Nếu đã có biểu đồ vẽ thành công, không hiển thị thông báo lỗi LLM
        if state.get("chart") is None:
            answer = f"{answer} (LLM fallback: {e})"

    return {**state, "answer": answer}
