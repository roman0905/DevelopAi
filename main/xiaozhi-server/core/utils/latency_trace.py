import json
import uuid
from typing import Any

from core.utils.latency_monitor import get_monitor


def _get_conn_id(conn: Any) -> str:
    session_id = getattr(conn, "session_id", None)
    if session_id:
        return str(session_id)
    return str(id(conn))


def _ensure_turn_id(conn: Any) -> str:
    sentence_id = getattr(conn, "sentence_id", None)
    if sentence_id:
        turn_id = str(sentence_id)
    else:
        turn_id = getattr(conn, "_latency_turn_id", None)
        if not turn_id:
            turn_id = uuid.uuid4().hex
            setattr(conn, "_latency_turn_id", turn_id)

    monitor = get_monitor()
    monitor.set_turn_id(turn_id)
    return turn_id


def begin_turn(conn: Any, user_text: str = "", source: str = "unknown") -> str:
    """Begin a conversational turn for stage tracing."""
    turn_id = _ensure_turn_id(conn)
    monitor = get_monitor()
    details = source
    if user_text:
        preview = user_text.replace("\n", " ")[:80]
        details = f"{source}; text={preview}"
    monitor.record_event(
        conn_id=_get_conn_id(conn),
        module="其他",
        stage="对话开始",
        elapsed_sec=0.0,
        turn_id=turn_id,
        details=details,
    )
    return turn_id


def mark_stage(conn: Any, stage: str, **kwargs: Any) -> None:
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
        module="其他",
        stage=stage,
        elapsed_sec=0.0,
        turn_id=turn_id,
        details=details,
    )
