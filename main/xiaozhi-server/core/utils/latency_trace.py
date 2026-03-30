import json
import re
from datetime import datetime
from typing import Any

from core.utils.latency_monitor import get_monitor


def _get_conn_id(conn: Any) -> str:
    session_id = getattr(conn, "session_id", None)
    if session_id:
        return str(session_id)
    return str(id(conn))


def _normalize_turn_text(user_text: str, max_len: int = 24) -> str:
    text = (user_text or "").strip()
    if not text:
        return "voice_input"

    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff _-]", "", text)
    text = text.strip(" _-")
    if not text:
        return "voice_input"
    return text[:max_len]


def build_turn_id(user_text: str = "", now: datetime | None = None) -> str:
    current = now or datetime.now()
    prefix = _normalize_turn_text(user_text)
    ts = current.strftime("%Y%m%d-%H%M%S-%f")[:-3]
    return f"{prefix}_{ts}"


def _infer_module(stage: str) -> str:
    stage_lower = stage.lower()
    if "listen" in stage_lower or "detect.text_ready" in stage_lower:
        return "输入接收"
    if "intent" in stage_lower or "detect" in stage_lower or "意图" in stage:
        return "意图识别"
    if "memory" in stage_lower or "query_memory" in stage_lower or "记忆" in stage:
        return "记忆查询"
    if "prefilter" in stage_lower or "route" in stage_lower or "前置" in stage:
        return "前置路由"
    if "chat" in stage_lower or "start_to_chat" in stage_lower or "对话" in stage:
        return "对话流程"
    if "vad" in stage_lower or "voice" in stage_lower or "语音检测" in stage:
        return "语音检测(VAD)"
    if "asr" in stage_lower or "语音识别" in stage:
        return "语音识别(ASR)"
    if "llm" in stage_lower or "推理" in stage or "大模型" in stage:
        return "大模型(LLM)"
    if "tts" in stage_lower or "合成" in stage:
        return "语音合成(TTS)"
    if "工具" in stage or "tool" in stage_lower or "function" in stage_lower:
        return "工具调用"
    return "其他"


def _ensure_turn_id(conn: Any) -> str:
    turn_id = getattr(conn, "turn_id", None)
    if not turn_id:
        preview = getattr(conn, "turn_text_preview", "")
        turn_id = build_turn_id(preview)
        setattr(conn, "turn_id", turn_id)

    monitor = get_monitor()
    monitor.set_turn_id(turn_id)
    return turn_id


def begin_turn(conn: Any, user_text: str = "", source: str = "unknown", force_new: bool = False) -> str:
    """Begin a conversational turn for stage tracing."""
    turn_id = getattr(conn, "turn_id", None)
    if force_new or not turn_id:
        turn_id = build_turn_id(user_text)
        setattr(conn, "turn_id", turn_id)
        setattr(conn, "turn_text_preview", _normalize_turn_text(user_text))

    monitor = get_monitor()
    monitor.set_turn_id(turn_id)
    details = source
    if user_text:
        preview = user_text.replace("\n", " ")[:80]
        details = f"{source}; text={preview}"
    monitor.record_event(
        conn_id=_get_conn_id(conn),
        module="输入接收",
        stage="对话开始",
        elapsed_sec=0.0,
        turn_id=turn_id,
        details=details,
    )
    return turn_id


def mark_stage(conn: Any, stage: str, module: str | None = None, **kwargs: Any) -> None:
    """Record an instant stage marker for latency timeline."""
    turn_id = _ensure_turn_id(conn)
    details = ""
    if kwargs:
        try:
            details = json.dumps(kwargs, ensure_ascii=False)
        except Exception:
            details = str(kwargs)

    monitor = get_monitor()
    monitor.record_event(
        conn_id=_get_conn_id(conn),
        module=module or _infer_module(stage),
        stage=stage,
        elapsed_sec=0.0,
        turn_id=turn_id,
        details=details,
    )


def start_stage(conn: Any, stage: str, turn_id: str | None = None) -> None:
    active_turn_id = turn_id or _ensure_turn_id(conn)
    monitor = get_monitor()
    monitor.set_turn_id(active_turn_id)
    monitor.start_timer(_get_conn_id(conn), stage)


def end_stage(
    conn: Any,
    stage: str,
    turn_id: str | None = None,
    details: str | None = None,
) -> float:
    active_turn_id = turn_id or _ensure_turn_id(conn)
    monitor = get_monitor()
    monitor.set_turn_id(active_turn_id)
    return monitor.end_timer(_get_conn_id(conn), stage, active_turn_id, details=details)
