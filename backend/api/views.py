from datetime import date
from typing import Any, Dict, List

import pandas as pd
import plotly.io as pio
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from graphs.djia_graph import run_djia_graph
from nodes.sql_executor import run_sql
from .models import Conversation, Message

# CSRF cho /api/ đã tắt ở api.middleware, quyền mặc định là AllowAny (core/settings.py)
User = get_user_model()


def _df_to_rows(df: pd.DataFrame, max_rows: int = 100) -> List[Dict[str, Any]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    # pd.Timestamp/datetime đều là date -> isoformat; NaN -> None để JSON hợp lệ
    rows = df.head(max_rows).map(lambda v: v.isoformat() if isinstance(v, date) else v)
    return rows.astype(object).where(rows.notna(), None).to_dict(orient="records")


@api_view(["POST"])
def djia_query(request):
    """
    Nhận câu hỏi tự nhiên, chạy multi-agent DJIA và trả về kết quả JSON cho FE.
    Đã đăng nhập thì lưu hội thoại vào DB; chưa đăng nhập thì chỉ trả kết quả.

    Body: { "question": "...", "force_chart": bool, "conversation_id": "uuid" }
    """
    question = (request.data.get("question") or "").strip()
    if not question:
        return Response({"detail": "Thiếu trường 'question'."}, status=400)

    conversation = None
    if request.user.is_authenticated:
        conversation_id = request.data.get("conversation_id")
        if conversation_id:
            conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
        else:
            conversation = Conversation.objects.create(user=request.user, title=question[:80])
        Message.objects.create(conversation=conversation, role=Message.ROLE_USER, content=question)

    try:
        result = run_djia_graph(question, force_chart=bool(request.data.get("force_chart")))
    except Exception as e:
        error = f"Lỗi nội bộ khi chạy agent: {e}"
        if conversation:
            Message.objects.create(
                conversation=conversation, role=Message.ROLE_ASSISTANT, content=error, error=str(e)
            )
        return Response({"success": False, "error": error}, status=500)

    chart_json = None
    if result.get("chart") is not None:
        try:
            chart_json = pio.to_json(result["chart"])
        except Exception:
            pass

    payload = {
        "success": bool(result.get("success")),
        "answer": result.get("answer", ""),
        "sql": result.get("sql") or "",
        "used_sample": bool(result.get("used_sample")),
        "error": result.get("error"),
        "rows": _df_to_rows(result.get("df")),
        "workflow": result.get("workflow", []),
        "complexity": result.get("complexity", {}),
        "chart_json": chart_json,
        "conversation_id": str(conversation.id) if conversation else None,
    }

    if conversation:
        Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_ASSISTANT,
            content=payload["answer"],
            sql=payload["sql"],
            used_sample=payload["used_sample"],
            error=str(payload["error"] or ""),
            rows_json=payload["rows"],
            metadata={
                "workflow": payload["workflow"],
                "complexity": payload["complexity"],
                "chart_json": chart_json,
            },
        )

    return Response(payload)


@api_view(["GET"])
def list_conversations(request):
    """Danh sách phiên chat của user (FE hiển thị ở sidebar)."""
    if not request.user.is_authenticated:
        return Response([])
    return Response(
        [
            {
                "id": str(conv.id),
                "title": conv.title,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat(),
            }
            for conv in Conversation.objects.filter(user=request.user)
        ]
    )


@api_view(["GET"])
def conversation_messages(request, conversation_id: str):
    """Toàn bộ message trong một phiên chat."""
    if not request.user.is_authenticated:
        return Response({"detail": "Cần đăng nhập."}, status=401)
    conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
    data = [
        {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "sql": msg.sql,
            "used_sample": msg.used_sample,
            "error": msg.error,
            "rows": msg.rows_json,
            "metadata": msg.metadata,
            "created_at": msg.created_at.isoformat(),
        }
        for msg in conversation.messages.all()
    ]
    return Response({"id": str(conversation.id), "title": conversation.title, "messages": data})


@api_view(["DELETE"])
def delete_conversation(request, conversation_id: str):
    """Xóa một phiên chat (và toàn bộ messages thuộc nó) của user hiện tại."""
    if not request.user.is_authenticated:
        return Response({"detail": "Cần đăng nhập."}, status=401)
    get_object_or_404(Conversation, id=conversation_id, user=request.user).delete()
    return Response({"success": True})


def _credentials(request):
    return (request.data.get("username") or "").strip(), request.data.get("password") or ""


@api_view(["POST"])
def login_view(request):
    """Đăng nhập sử dụng session Django."""
    username, password = _credentials(request)
    if not username or not password:
        return Response({"detail": "Thiếu username hoặc password."}, status=400)
    user = authenticate(request, username=username, password=password)
    if user is None:
        return Response({"detail": "Sai tên đăng nhập hoặc mật khẩu."}, status=400)
    login(request, user)
    return Response({"username": user.username})


@api_view(["POST"])
def register_view(request):
    """Đăng ký tài khoản mới (đơn giản: username + password)."""
    username, password = _credentials(request)
    if not username or not password:
        return Response({"detail": "Thiếu username hoặc password."}, status=400)
    if User.objects.filter(username=username).exists():
        return Response({"detail": "Username đã tồn tại."}, status=400)
    User.objects.create_user(username=username, password=password)
    return Response({"username": username})


@api_view(["POST"])
def logout_view(request):
    logout(request)
    return Response({"success": True})


@api_view(["GET"])
def me_view(request):
    """Thông tin user hiện tại (401 nếu chưa đăng nhập)."""
    if not request.user.is_authenticated:
        return Response({"detail": "Chưa đăng nhập."}, status=401)
    return Response({"username": request.user.username})


@api_view(["POST"])
def execute_sql(request):
    """
    Chạy SQL trực tiếp trên database (không qua LLM). Kết nối read-only nên chỉ SELECT được.

    Body: { "sql": "SELECT * FROM prices WHERE ticker = 'AAPL' LIMIT 10" }
    """
    sql = (request.data.get("sql") or "").strip()
    if not sql:
        return Response({"detail": "Thiếu trường 'sql'."}, status=400)
    try:
        df, display_sql = run_sql(sql, {})
    except Exception as e:
        return Response({"success": False, "rows": [], "sql": sql, "error": str(e)}, status=500)
    return Response({"success": True, "rows": _df_to_rows(df), "sql": display_sql, "error": None})
