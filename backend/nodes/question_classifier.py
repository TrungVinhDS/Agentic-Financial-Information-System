"""
Question Classifier Node - Phân loại câu hỏi ngay từ đầu.

- SQL-related: Câu hỏi cần truy vấn database (giá, volume, số liệu)
- Other: Câu hỏi không liên quan SQL (kiến thức tổng quát, câu hỏi chung)
"""

from typing import Dict, Any

from nodes.llm import ask


def classify_question(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph Node: thêm key is_sql_related vào state.

    Mặc định True (đi nhánh SQL) khi câu hỏi rỗng hoặc LLM lỗi/thiếu API key.
    """
    question = state.get("question", "")
    if not question.strip():
        return {**state, "is_sql_related": True}

    prompt = f"""Bạn là một chuyên gia phân loại câu hỏi cho hệ thống tài chính.

Hệ thống có 2 loại câu hỏi:
1. SQL-RELATED: Câu hỏi yêu cầu dữ liệu cụ thể từ database
   Bao gồm:
   - Giá cổ phiếu, volume, số liệu cụ thể: "What was the price of Apple on 2024-01-15?", "Plot the volume",
     "Compare prices of AAPL and MSFT", "Average closing price in 2024"
   - Metadata của công ty (website, sector, industry, country, market_cap, pe_ratio, dividend_yield, etc.):
     "Website of 3M Company?", "What is the sector of Apple?", "Market cap of Microsoft?",
     "Dividend yield of Coca-Cola?", "Industry of Boeing?", "Country of IBM?"
   - Ticker symbol: "What is the ticker symbol for Apple?", "Symbol of Microsoft?"
   - So sánh số liệu, biểu đồ dữ liệu, truy vấn theo ngày tháng, ticker cụ thể

2. OTHER: Câu hỏi không cần SQL (kiến thức tổng quát, giải thích, định nghĩa, câu hỏi chung)
   Ví dụ: "What is DJIA?", "How does stock market work?", "Giải thích về thị trường chứng khoán",
          "What is a dividend?", "Tôi muốn biết về đầu tư", "How to invest in stocks?"

CÂU HỎI: {question}

NHIỆM VỤ: Xác định xem câu hỏi này có liên quan đến SQL database không.

QUY TẮC QUAN TRỌNG:
- ⚠️ TRẢ VỀ SQL nếu câu hỏi yêu cầu BẤT KỲ thông tin nào từ database, bao gồm:
  * Giá cả, volume, số liệu cụ thể
  * Metadata của công ty (website, sector, industry, country, market_cap, pe_ratio, dividend_yield,
    week_52_high, week_52_low, description, ticker symbol)
  * So sánh số liệu, biểu đồ dữ liệu
  * Truy vấn theo ngày tháng, ticker cụ thể

- ⚠️ TRẢ VỀ OTHER CHỈ KHI câu hỏi hỏi về: khái niệm, định nghĩa, giải thích, kiến thức tổng quát,
  cách thức hoạt động, câu hỏi chung KHÔNG cần dữ liệu cụ thể từ database

LƯU Ý: Câu hỏi về website, sector, industry, country, market_cap, pe_ratio, dividend_yield của một công ty cụ thể
→ LUÔN LUÔN là SQL-RELATED (vì cần truy vấn từ bảng companies trong database)

CHỈ TRẢ LỜI: SQL hoặc OTHER (không có dấu chấm, không có giải thích thêm)"""

    try:
        return {**state, "is_sql_related": "SQL" in ask(prompt).upper()}
    except Exception as e:
        print(f"Error in classify_question with LLM: {e}")
        return {**state, "is_sql_related": True}
