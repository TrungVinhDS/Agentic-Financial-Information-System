"""
DJIA Multi-Agent Workflow - LangGraph Implementation.

Module này định nghĩa workflow chính của hệ thống multi-agent,
sử dụng LangGraph để điều phối các nodes chuyên biệt.

WORKFLOW (với Classifier và RAG):
┌─────────────────────────────────────────────────────────────┐
│                     START: User Question                     │
└──────────────────────┬──────────────────────────────────────┘
                       ↓
           ┌───────────────────────┐
           │ 0. Question Classifier │  Phân loại SQL/Other
           │(question_classifier)   │
           └───────────┬───────────┘
                       │
            SQL?      │
              ↙        ↘
         SQL ↙            ↘ Other
           ↙                ↘
    ┌───────────┐     ┌──────────────────┐
    │ 1. Planner│     │  2. RAG Retriever│
    │(plan_query)│     │ (rag_retrieve)   │
    └─────┬─────┘     └────────┬─────────┘
          ↓                    │
    ┌──────────────┐    RAG có thể trả lời?
    │ 3. SQL Match │           ↙    ↘
    └─────┬────────┘      Có ↙        ↘ Không
          ↓                ↙            ↘
    ┌──────────────┐   ┌──────┐    ┌──────────────────┐
    │ 4. SQL Gen   │   │ END  │    │ 7. Answer Summary │
    └─────┬────────┘   │(PDF) │    │ (LLM General)    │
          ↓            └──────┘    └────────┬─────────┘
    ┌──────────────┐                        ↓
    │ 5. SQL Exec  │                ┌───────────────┐
    └─────┬────────┘                │  END: Answer  │
          ↓                         └───────────────┘
    Cần biểu đồ?
         ↙    ↘
    Có ↙        ↘ Không
       ↙            ↘
┌──────────┐    ┌──────────────────┐
│ 6. Chart │ →  │ 7. Answer Summary│
└──────────┘    └────────┬──────────┘
                        ↓
                ┌───────────────┐
                │  END: Answer  │
                └───────────────┘
"""

from typing import Dict, Any
from langgraph.graph import StateGraph, END

# Import các nodes từ nodes/
from nodes.question_classifier import classify_question
from nodes.planner import plan_query
from nodes.rag_retriever import rag_retrieve
from nodes.sql_template_matcher import match_sql_template
from nodes.sql_llm_generator import generate_sql
from nodes.sql_executor import execute_sql
from nodes.answer_summarizer import summarize_answer
from nodes.chart_generator import generate_chart


def build_djia_graph():
    """
    Xây dựng LangGraph workflow với các nodes và edges.

    Workflow gồm 8 nodes chính:
    0. question_classifier: Phân loại câu hỏi (SQL-related hay Other)
    1. plan_query: Phân tích câu hỏi SQL, xác định độ phức tạp
    2. rag_retrieve: Xử lý câu hỏi Other từ knowledge base (PDF)
    3. match_sql_template: Tìm SQL mẫu từ 80+ templates
    4. generate_sql: Sinh SQL bằng LLM (nếu không có mẫu)
    5. execute_sql: Thực thi SQL trên PostgreSQL database
    6. generate_chart: Vẽ biểu đồ (nếu cần)
    7. summarize_answer: Tạo câu trả lời tự nhiên (SQL hoặc LLM general)

    Returns:
        Compiled LangGraph workflow ready để invoke
    """
    # Khởi tạo StateGraph với state type là dictionary
    graph = StateGraph(dict)

    # ========== THÊM NODES VÀO GRAPH ==========
    graph.add_node("question_classifier", classify_question)
    graph.add_node("plan_query", plan_query)
    graph.add_node("rag_retrieve", rag_retrieve)
    graph.add_node("match_sql_template", match_sql_template)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("execute_sql", execute_sql)
    graph.add_node("generate_chart", generate_chart)
    graph.add_node("summarize_answer", summarize_answer)

    # ========== ĐỊNH NGHĨA WORKFLOW FLOW ==========

    # Start: Bắt đầu với classifier
    graph.set_entry_point("question_classifier")

    # Step 0→1/2: SQL-related → SQL pipeline, Other → RAG
    graph.add_conditional_edges(
        "question_classifier",
        lambda state: "plan_query" if state.get("is_sql_related", True) else "rag_retrieve",
        ["plan_query", "rag_retrieve"],
    )

    # Step 1→3: SQL pipeline - Sau planning → SQL template matching
    graph.add_edge("plan_query", "match_sql_template")

    # Step 2→7: Sau RAG → luôn chuyển sang answer_summarizer
    # (answer_summarizer sẽ quyết định dùng RAG context hay LLM general)
    graph.add_edge("rag_retrieve", "summarize_answer")

    # Step 3→4/5: Không tìm thấy SQL mẫu → gọi LLM sinh SQL
    graph.add_conditional_edges(
        "match_sql_template",
        lambda state: "execute_sql" if state.get("sql") else "generate_sql",
        ["generate_sql", "execute_sql"],
    )

    # Step 4→5: Sau generate SQL → execute
    graph.add_edge("generate_sql", "execute_sql")

    # Step 5→6/7: Vẽ biểu đồ nếu planner đã set needs_chart
    graph.add_conditional_edges(
        "execute_sql",
        lambda state: "generate_chart" if state.get("needs_chart") else "summarize_answer",
        ["generate_chart", "summarize_answer"],
    )

    # Step 6→7: Sau vẽ biểu đồ → summarize
    graph.add_edge("generate_chart", "summarize_answer")

    # Step 7→END: Kết thúc workflow
    graph.add_edge("summarize_answer", END)

    # Compile graph thành executable workflow
    return graph.compile()


# Compile một lần khi import, dùng lại cho mọi request
APP = build_djia_graph()


def run_djia_graph(question: str, force_chart: bool = False) -> Dict[str, Any]:
    """
    Entry point chính để chạy workflow DJIA.

    Hàm này:
    1. Invoke workflow với câu hỏi từ user
    2. Track workflow steps để debug
    3. Trả về kết quả cuối cùng với metadata

    Args:
        question: Câu hỏi từ người dùng (tiếng Việt hoặc tiếng Anh)
        force_chart: Force vẽ biểu đồ bất kể câu hỏi có chứa keyword "plot" hay không

    Returns:
        Dictionary chứa:
        - success: Boolean - workflow thành công hay không
        - answer: String - câu trả lời tự nhiên
        - sql: String - SQL đã thực thi (để hiển thị)
        - df: DataFrame - dữ liệu kết quả
        - chart: Plotly Figure - biểu đồ (nếu có)
        - used_sample: Boolean - có dùng SQL mẫu không
        - error: String - error message (nếu có lỗi)
        - workflow: List - các bước đã thực hiện
        - complexity: Dict - thông tin độ phức tạp
        - is_general_question: Boolean - có phải general question không

    Examples:
        >>> result = run_djia_graph("What was Apple's closing price on 2024-01-15?")
        >>> print(result['answer'])
        'The closing price of Apple on January 15, 2024 was $185.92'

        >>> result = run_djia_graph("What is DJIA?")
        >>> print(result['is_general_question'])
        True

        >>> result = run_djia_graph("Vẽ biểu đồ giá Apple trong Q1 2024")
        >>> result['chart']  # Plotly figure object
    """
    # Track workflow steps để debugging/logging
    workflow_steps = []

    # Invoke workflow với initial state
    initial_state = {"question": question}
    if force_chart:
        initial_state["force_chart"] = True
    result = APP.invoke(initial_state)

    # ========== TRACK WORKFLOW STEPS ==========

    # Step 0: Classification
    is_sql_related = result.get("is_sql_related", True)
    workflow_steps.append(
        {
            "step": 1,
            "node": "question_classifier",
            "description": "Phân loại câu hỏi",
            "status": "completed",
            "result": f"Loại: {'SQL-related' if is_sql_related else 'Other'}",
        }
    )

    # Nếu không phải SQL-related, xử lý RAG path
    if not is_sql_related:
        has_rag_context = result.get("has_rag_context", False)
        workflow_steps.append(
            {
                "step": 2,
                "node": "rag_retrieve",
                "description": "RAG - Tìm kiếm trong PDF documents",
                "status": "completed",
                "result": f"{'Tìm thấy thông tin liên quan trong PDF' if has_rag_context else 'Không có thông tin trong PDF'}",
            }
        )
        
        # Answer summarizer đã xử lý (dùng RAG context hoặc LLM general)
        workflow_steps.append(
            {
                "step": 3,
                "node": "summarize_answer",
                "description": f"Trả lời câu hỏi ({'dựa trên PDF' if has_rag_context else 'LLM general'})",
                "status": "completed",
                "result": "Đã tạo câu trả lời",
            }
        )
        
        return {
            "success": True,
            "sql": None,
            "df": None,
            "answer": result.get("answer", ""),
            "used_sample": False,
            "error": None,
            "workflow": workflow_steps,
            "chart": None,
            "complexity": {},
            "is_general_question": not is_sql_related,
        }

    # SQL-related path: Lấy thông tin complexity
    complexity = result.get("complexity", {})

    # Step 1: Planning (chỉ log nếu là multi-step query)
    if complexity.get("is_multi_step"):
        workflow_steps.append(
            {
                "step": len(workflow_steps) + 1,
                "node": "plan_query",
                "description": "Phân tích và lên kế hoạch cho câu hỏi phức tạp",
                "status": "completed",
                "result": f"Độ phức tạp: Multi-step, Cần biểu đồ: {complexity.get('needs_chart')}",
            }
        )

    # Step 1: SQL Template Matching
    workflow_steps.append(
        {
            "step": len(workflow_steps) + 1,
            "node": "match_sql_template",
            "description": "Trích xuất ticker và tìm SQL mẫu phù hợp",
            "status": "completed",
            "result": f"Ticker: {result.get('ticker', 'N/A')}, SQL mẫu: {'✓' if result.get('used_sample') else '✗'}",
        }
    )

    # Step 2: SQL Generation (nếu không có mẫu)
    if not result.get("used_sample"):
        workflow_steps.append(
            {
                "step": len(workflow_steps) + 1,
                "node": "generate_sql",
                "description": "Sinh SQL bằng LLM",
                "status": "completed",
                "result": "SQL được sinh tự động bởi LLM",
            }
        )

    # Step 3: SQL Execution
    workflow_steps.append(
        {
            "step": len(workflow_steps) + 1,
            "node": "execute_sql",
            "description": "Thực thi SQL trên PostgreSQL database",
            "status": "completed" if not result.get("error") else "error",
            "result": (
                f"Trả về {len(result.get('df', []))} dòng dữ liệu"
                if result.get("df") is not None
                else "Lỗi thực thi"
            ),
        }
    )

    # Step 4: Chart Generation (nếu cần)
    if result.get("needs_chart"):
        workflow_steps.append(
            {
                "step": len(workflow_steps) + 1,
                "node": "generate_chart",
                "description": f"Tạo biểu đồ {result.get('chart_type', 'line')}",
                "status": "completed" if result.get("chart") else "skipped",
                "result": f"Biểu đồ: {'✓ Đã tạo' if result.get('chart') else '✗ Không tạo được'}",
            }
        )

    # Step 5: Answer Summarization
    workflow_steps.append(
        {
            "step": len(workflow_steps) + 1,
            "node": "summarize_answer",
            "description": "Tạo câu trả lời tự nhiên từ kết quả",
            "status": "completed",
            "result": f"Câu trả lời: {'✓' if result.get('answer') else '✗'}",
        }
    )

    # ========== VALIDATE KẾT QUẢ ==========
    has_error = result.get("error") is not None
    has_answer = bool(result.get("answer", "").strip())
    has_data = result.get("df") is not None and not result.get("df").empty

    # Workflow thành công nếu không có lỗi và có kết quả (answer hoặc data)
    success = not has_error and (has_answer or has_data)

    # ========== CHUẨN HÓA OUTPUT ==========
    return {
        "success": success,  # Workflow thành công?
        "sql": result.get("actual_sql", result.get("sql", "")),  # SQL đã thực thi
        "df": result.get("df"),  # DataFrame kết quả
        "answer": result.get("answer", ""),  # Câu trả lời tự nhiên
        "used_sample": result.get("used_sample", False),  # Có dùng SQL mẫu?
        "error": result.get("error"),  # Error message (nếu có)
        "workflow": workflow_steps,  # Steps đã thực hiện
        "chart": result.get("chart"),  # Plotly figure (nếu có)
        "complexity": complexity,  # Thông tin độ phức tạp
        "is_general_question": False,  # Đây là data question
    }
