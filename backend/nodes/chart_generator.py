from typing import Dict, Any, Optional

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

from nodes.llm import ask
from nodes.sql_executor import run_sql
from nodes.utils import (
    extract_ticker,
    extract_date_range,
    extract_date_parts,
    normalize_text,
)


def build_chart_code(
    question: str, chart_type_hint: Optional[str], df: pd.DataFrame
) -> Optional[str]:
    """
    Yêu cầu LLM sinh code Plotly để dựng biểu đồ trực tiếp từ DataFrame df.
    """
    if df is None or df.empty:
        return None

    # 60 dòng đầu làm dữ liệu mẫu cho LLM (ngày ở dạng YYYY-MM-DD)
    preview = df.head(60).copy()
    for col in preview.select_dtypes("datetime").columns:
        preview[col] = preview[col].dt.strftime("%Y-%m-%d")
    data_json = preview.to_json(orient="records", force_ascii=False, default_handler=str)
    columns = ", ".join(df.columns.tolist())

    system_prompt = (
        "Bạn là trợ lý dựng biểu đồ Plotly bằng Python. "
        "Hãy đọc câu hỏi và dữ liệu mẫu, sau đó sinh code Python (không markdown) tạo đối tượng Plotly Figure."
    )

    # Constraints cho visualization
    constraints = (
        "=== QUY TẮC QUAN TRỌNG ===\n"
        "1. Khi câu hỏi về 'per company', 'each company', 'for each DJIA company':\n"
        "   → Mỗi điểm/bar/slice = một công ty\n"
        "   → Nếu df chưa được groupby theo ticker/symbol, PHẢI dùng df.groupby('ticker') hoặc df.groupby('symbol')\n"
        "   → KHÔNG groupby theo date khi câu hỏi về per company\n\n"
        "2. Scatter plot:\n"
        "   → Mỗi điểm = một company (nếu 'per company')\n"
        "   → Dùng go.Scatter hoặc px.scatter với x và y từ df\n"
        "   → Thêm text=name hoặc text=ticker để hiển thị nhãn công ty\n\n"
        "3. Bar chart:\n"
        "   → Một bar = một entity (company hoặc day)\n"
        "   → Nếu 'per company' → x=ticker/name, y=metric\n"
        "   → Có thể sort theo y: df = df.sort_values('metric', ascending=False)\n\n"
        "4. Pie chart:\n"
        "   → Một slice = một category (sector, industry...)\n"
        "   → Nếu df có cột sector và market_cap (hoặc metric khác): dùng trực tiếp df, không cần groupby lại\n"
        "   → Nếu df chưa được aggregate và cần count: df = df.groupby('sector').size().reset_index(name='count')\n"
        "   → labels=category_column (sector/industry), values=metric_column (market_cap/sum/count)\n"
        "   → Dùng go.Pie hoặc px.pie\n\n"
        "5. Time series line chart:\n"
        "   → Khi câu hỏi về 'time series', 'over time', 'from ... to ...', 'during ...', 'closing price over time':\n"
        "   → Dùng go.Scatter với mode='lines' (KHÔNG phải 'markers')\n"
        "   → x=df['date'], y=df['close'] (hoặc cột giá khác như 'open', 'high', 'low')\n"
        "   → Đảm bảo cột date đã là datetime (thường đã được xử lý)\n"
        "   → Thêm hovertemplate để hiển thị thông tin khi hover\n"
        "   → Format ngày trên trục x: figure.update_xaxes(tickformat='%Y-%m-%d')\n\n"
        "6. BẮT BUỘC sử dụng DataFrame df có sẵn, KHÔNG hard-code dữ liệu.\n"
        "7. Code phải gán kết quả vào biến 'figure' (go.Figure).\n"
        "8. Không in/log, không markdown, không tiền tố 'python'.\n\n"
    )

    # Few-shot examples
    examples = (
        "=== VÍ DỤ ĐÚNG (Few-shot Examples) ===\n\n"
        "Example 1: Scatter plot market cap vs P/E for all companies\n"
        "DataFrame có: symbol, name, market_cap, pe_ratio\n"
        "Code:\n"
        "figure = go.Figure()\n"
        "figure.add_trace(go.Scatter(\n"
        "    x=df['market_cap'],\n"
        "    y=df['pe_ratio'],\n"
        "    mode='markers+text',\n"
        "    text=df['name'],\n"
        "    textposition='top center',\n"
        "    name='Companies'\n"
        "))\n"
        "figure.update_layout(title='Market Cap vs P/E Ratio', xaxis_title='Market Cap', yaxis_title='P/E Ratio')\n\n"
        "Example 2: Pie chart distribution by sector (count)\n"
        "DataFrame có: sector (có thể lặp lại)\n"
        "Code:\n"
        "sector_counts = df.groupby('sector').size().reset_index(name='count')\n"
        "figure = go.Figure(data=[go.Pie(labels=sector_counts['sector'], values=sector_counts['count'])])\n"
        "figure.update_layout(title='Distribution by Sector')\n\n"
        "Example 2b: Pie chart market cap proportions by sector\n"
        "DataFrame có: sector, và một cột chứa tổng market_cap (ví dụ: 'SUM(c.market_cap)' hoặc tên khác)\n"
        "Code:\n"
        "# Xác định tên cột market_cap từ df.columns (thường chứa 'market' hoặc 'sum')\n"
        "# Nếu không chắc, dùng df.columns[1] (cột thứ 2) hoặc kiểm tra df.columns\n"
        "value_col = [col for col in df.columns if col != 'sector'][0]  # Lấy cột đầu tiên không phải sector\n"
        "figure = go.Figure(data=[go.Pie(labels=df['sector'], values=df[value_col], textinfo='label+percent')])\n"
        "figure.update_layout(title='Market Capitalization Proportions by Sector')\n\n"
        "Example 2c: Bar chart market cap by sector\n"
        "DataFrame có: sector, và một cột chứa tổng market_cap\n"
        "Code:\n"
        "# Xác định tên cột market_cap\n"
        "value_col = [col for col in df.columns if col != 'sector'][0]  # Lấy cột đầu tiên không phải sector\n"
        "df_sorted = df.sort_values(value_col, ascending=False)\n"
        "figure = go.Figure(data=[go.Bar(x=df_sorted['sector'], y=df_sorted[value_col])])\n"
        "figure.update_layout(title='Market Capitalization by Sector', xaxis_title='Sector', yaxis_title='Market Cap')\n\n"
        "Example 3: Bar chart total dividends per company in 2024\n"
        "DataFrame có: ticker, name, total_dividends\n"
        "Code:\n"
        "df_sorted = df.sort_values('total_dividends', ascending=False)\n"
        "figure = go.Figure(data=[go.Bar(x=df_sorted['name'], y=df_sorted['total_dividends'])])\n"
        "figure.update_layout(title='Total Dividends per Company in 2024', xaxis_title='Company', yaxis_title='Total Dividends')\n\n"
        "Example 3b: Bar chart top 10 companies by market cap\n"
        "DataFrame có: symbol (hoặc ticker), name, market_cap (đã được ORDER BY market_cap DESC LIMIT 10)\n"
        "Code:\n"
        "# DataFrame đã được sort, chỉ cần vẽ\n"
        "figure = go.Figure(data=[go.Bar(x=df['name'], y=df['market_cap'])])\n"
        "figure.update_layout(title='Top 10 Companies by Market Capitalization', xaxis_title='Company', yaxis_title='Market Cap', xaxis_tickangle=-45)\n\n"
        "Example 4: Scatter plot average volume vs average price per company\n"
        "DataFrame có: ticker, name, avg_volume, avg_close\n"
        "Code:\n"
        "figure = go.Figure()\n"
        "figure.add_trace(go.Scatter(\n"
        "    x=df['avg_volume'],\n"
        "    y=df['avg_close'],\n"
        "    mode='markers+text',\n"
        "    text=df['name'],\n"
        "    textposition='top center',\n"
        "    name='Companies'\n"
        "))\n"
        "figure.update_layout(title='Average Volume vs Average Price', xaxis_title='Avg Volume', yaxis_title='Avg Price')\n\n"
        "Example 5: Time series line chart of closing price\n"
        "DataFrame có: date, close, volume (cột date đã là datetime)\n"
        "Code:\n"
        "figure = go.Figure()\n"
        "figure.add_trace(go.Scatter(\n"
        "    x=df['date'],\n"
        "    y=df['close'],\n"
        "    mode='lines',\n"
        "    name='Close Price',\n"
        "    line=dict(color='#2E86AB', width=2),\n"
        "    hovertemplate='Date: %{x|%Y-%m-%d}<br>Close: $%{y:.2f}<extra></extra>'\n"
        "))\n"
        "figure.update_layout(\n"
        "    title='Closing Price Over Time',\n"
        "    xaxis_title='Date',\n"
        "    yaxis_title='Price ($)',\n"
        "    hovermode='x unified',\n"
        "    xaxis=dict(tickformat='%Y-%m-%d')\n"
        ")\n\n"
        "❌ SAI: Không groupby khi cần aggregate per company\n"
        "❌ SAI: Groupby theo date khi câu hỏi về per company\n"
        "❌ SAI: Hard-code dữ liệu thay vì dùng df\n"
        "❌ SAI: Dùng mode='markers' thay vì mode='lines' cho time series\n\n"
    )

    guidance = (
        "=== YÊU CẦU KỸ THUẬT ===\n"
        "- DataFrame có tên là df (pandas DataFrame). Có thể sử dụng: pd, np, go, px, make_subplots.\n"
        "- TÊN CỘT ĐÃ ĐƯỢC NORMALIZE VỀ LOWERCASE: date, close, open, high, low, volume, ticker, symbol, name, sector, market_cap, ...\n"
        "- Code phải gán kết quả cuối cùng vào biến 'figure' kiểu plotly.graph_objects.Figure.\n"
        "- Nếu cần transform (groupby, pivot, tính toán…), hãy thực hiện trong code.\n"
        "- Tự động suy luận loại biểu đồ phù hợp nếu câu hỏi không rõ.\n"
        "- BẮT BUỘC sử dụng chính DataFrame df; không tái tạo dữ liệu thủ công.\n"
        "- Không in/log, không viết markdown, không thêm tiền tố như 'python'. Chỉ trả về code Python thuần.\n"
        "- Với comparison nhiều ticker, lọc/loop theo ticker. Với correlation heatmap, tạo ma trận tương quan.\n"
        "- Giữ số trace vừa phải (≤5). Đặt tiêu đề, nhãn trục rõ ràng. Dùng format ngày chuẩn nếu trục x là thời gian.\n"
        "- QUAN TRỌNG: Khi DataFrame có cột với tên phức tạp (ví dụ 'sum(c.market_cap)'), hãy:\n"
        "  * Sử dụng tên cột chính xác từ df.columns (đã lowercase)\n"
        "  * Hoặc dùng df.columns[0], df.columns[1]... để tham chiếu theo vị trí\n"
        "  * Hoặc tìm cột bằng cách: [col for col in df.columns if 'keyword' in col.lower()][0]\n"
        "- Với pie/bar chart về sector: nếu df có cột 'sector' và một cột metric (market_cap, count...), dùng trực tiếp các cột đó.\n"
        "- Với time series: cột 'date' đã là datetime, dùng trực tiếp df['date'] và df['close'] (hoặc open/high/low).\n"
    )

    prompt = (
        f"{system_prompt}\n\n"
        f"{constraints}\n"
        f"{examples}\n"
        f"{guidance}\n\n"
        f"=== THÔNG TIN CÂU HỎI VÀ DỮ LIỆU ===\n"
        f"Câu hỏi: {question}\n"
        f"Chart type hint: {chart_type_hint or 'auto'}\n"
        f"Các cột sẵn có trong df: {columns}\n"
        f"Dữ liệu mẫu (JSON, {len(preview)} dòng đầu):\n{data_json}\n\n"
        f"Hãy sinh code Python để tạo biểu đồ Plotly phù hợp với câu hỏi và dữ liệu trên."
    )

    try:
        code = ask(prompt)
        if code.startswith("```"):
            parts = code.split("```")
            code = parts[1] if len(parts) > 1 else code
        return code.strip()
    except Exception as e:
        print(f"Error generating chart code with LLM: {e}")
        return None


def render_chart_from_code(
    code: Optional[str], df: pd.DataFrame
) -> Optional[go.Figure]:
    if not code:
        return None
    code = code.strip()
    if code.lower().startswith("python"):
        newline_idx = code.find("\n")
        code = code[newline_idx + 1 :] if newline_idx != -1 else ""
    local_env: Dict[str, Any] = {
        "pd": pd,
        "np": np,
        "go": go,
        "px": px,
        "make_subplots": make_subplots,
        "df": df.copy(),
    }
    try:
        exec(code, {}, local_env)
    except Exception as e:
        print(f"Error executing chart code: {e}\nCode:\n{code}")
        return None

    figure = local_env.get("figure")
    if isinstance(figure, go.Figure):
        return figure

    build_fn = local_env.get("build_chart")
    if callable(build_fn):
        try:
            figure = build_fn(df.copy())
            if isinstance(figure, go.Figure):
                return figure
        except Exception as e:
            print(f"Error calling build_chart(): {e}")

    for value in local_env.values():
        if isinstance(value, go.Figure):
            return value

    print("LLM code did not produce a Plotly Figure.")
    return None


def fetch_chart_data(question: str, ticker: str, chart_type: str) -> pd.DataFrame:
    """Fallback khi SQL của agent không trả về dòng nào: lấy thẳng giá từ bảng prices để vẽ."""
    params: Dict[str, Any] = {}
    if chart_type == "comparison":
        # Tất cả công ty trong 1 tháng gần nhất có dữ liệu
        sql = """
            SELECT date, ticker, open, high, low, close, volume
            FROM prices
            WHERE date >= (SELECT MAX(date) FROM prices) - INTERVAL '1 month'
            ORDER BY date ASC, ticker ASC
        """
    else:
        start_date, end_date = extract_date_range(question)
        date_parts = extract_date_parts(question)
        params["ticker"] = ticker
        if start_date and end_date:
            where = "date BETWEEN CAST(:start_date AS DATE) AND CAST(:end_date AS DATE)"
            params.update(start_date=start_date, end_date=end_date)
        elif "year" in date_parts:
            where = "EXTRACT(YEAR FROM date) = :year"
            params["year"] = int(date_parts["year"])
            if "month" in date_parts:
                where += " AND EXTRACT(MONTH FROM date) = :month"
                params["month"] = int(date_parts["month"])
        else:
            # Mặc định: 3 tháng gần nhất có dữ liệu
            where = "date >= (SELECT MAX(date) FROM prices) - INTERVAL '3 months'"
        sql = f"""
            SELECT date, open, high, low, close, volume
            FROM prices
            WHERE ticker = :ticker AND {where}
            ORDER BY date ASC
        """
    try:
        return run_sql(sql, params)[0]
    except Exception as e:
        print(f"Error fetching chart data: {e}")
        return pd.DataFrame()


def generate_chart(state: Dict[str, Any]) -> Dict[str, Any]:
    """Node tạo biểu đồ dựa trên yêu cầu."""
    question = state.get("question", "")
    ticker = state.get("ticker")
    needs_chart = state.get("needs_chart", False)
    chart_type = state.get("chart_type", "line")
    df = state.get("df")

    if not needs_chart:
        return {**state, "chart": None}

    # Kiểm tra nếu câu hỏi về tất cả DJIA companies, top N companies, hoặc sector/industry
    q = normalize_text(question)
    is_all_companies = any(
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
            "top ",
            "top companies",
            "top 10",
            "top 5",
            "top 20",  # Top N companies
            "largest companies",
            "biggest companies",
            "highest companies",
        ]
    )
    is_sector_query = any(
        word in q for word in ["sector", "industry", "ngành", "lĩnh vực"]
    )

    # Nếu không có ticker, thử trích xuất (trừ khi là all companies hoặc sector query)
    if not ticker and not is_all_companies and not is_sector_query:
        ticker = extract_ticker(question)

    # Nếu vẫn không có ticker và không phải all companies/sector, không vẽ được
    if (
        not ticker
        and chart_type != "comparison"
        and not is_all_companies
        and not is_sector_query
    ):
        return {
            **state,
            "chart": None,
            "chart_error": "Không xác định được mã cổ phiếu",
        }

    # Nếu đã có df từ SQL execution, dùng luôn (không fetch lại)
    # Với all companies hoặc sector query, SQL đã trả về đúng dữ liệu
    # Giữ nguyên df kể cả khi không có cột date (ví dụ correlation matrix, aggregate)
    if not is_all_companies and not is_sector_query:
        if df is None or df.empty:
            df = fetch_chart_data(question, ticker, chart_type)

    if df is None or df.empty:
        return {**state, "chart": None, "chart_error": "Không có dữ liệu để vẽ biểu đồ"}

    # Normalize column names to lowercase để tránh vấn đề case sensitivity
    df.columns = [col.lower() for col in df.columns]

    # Ensure date column is datetime (nếu có)
    if "date" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["date"]):
        df["date"] = pd.to_datetime(df["date"])

    chart = None
    error_msg = None
    try:
        code = build_chart_code(question, chart_type, df)
        if not code:
            error_msg = "LLM không thể sinh code Python để vẽ biểu đồ."
        else:
            chart = render_chart_from_code(code, df)
            if chart is None:
                error_msg = f"Code Python được sinh ra không tạo được biểu đồ Plotly hợp lệ. Code:\n{code[:500]}..."
    except Exception as e:
        error_msg = f"Lỗi khi sinh/thực thi code biểu đồ: {str(e)}"
        print(f"Error generating chart code via LLM: {e}")

    if chart is None:
        return {
            **state,
            "chart": None,
            "chart_error": error_msg
            or "Không thể tạo biểu đồ từ mô tả của bạn. Hãy cụ thể hơn hoặc thử lại.",
        }

    # Trả df lại vào state: nếu phải tự fetch dữ liệu (SQL rỗng), câu trả lời sẽ dùng đúng dữ liệu đã vẽ
    return {**state, "df": df, "chart": chart, "chart_error": None}
