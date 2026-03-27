import json
import threading
import time
import uuid
from typing import Any, Optional

TAG = __name__


def _now_perf() -> float:
    return time.perf_counter()


def _safe_text_preview(text: Optional[str], limit: int = 80) -> str:
    if not text:
        return ""
    text = str(text).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _get_trace_store(conn) -> dict[str, Any]:
    if not hasattr(conn, "latency_traces") or conn.latency_traces is None:
        conn.latency_traces = {}
    return conn.latency_traces


def _get_trace_lock(conn) -> threading.Lock:
    if not hasattr(conn, "latency_trace_lock") or conn.latency_trace_lock is None:
        conn.latency_trace_lock = threading.Lock()
    return conn.latency_trace_lock


def begin_turn(conn, text: str = "", source: str = "listen_detect") -> str:
    turn_id = uuid.uuid4().hex[:8]
    trace = {
        "turn_id": turn_id,
        "session_id": getattr(conn, "session_id", ""),
        "source": source,
        "text": _safe_text_preview(text),
        "created_at": time.time(),
        "start_perf": _now_perf(),
        "events": [],
        "completed": False,
    }

    lock = _get_trace_lock(conn)
    store = _get_trace_store(conn)
    with lock:
        store[turn_id] = trace
        conn.current_turn_id = turn_id

    mark_stage(conn, "listen.detect.received", turn_id=turn_id)
    return turn_id


def mark_stage(conn, stage: str, turn_id: Optional[str] = None, **extra) -> None:
    selected_turn_id = turn_id or getattr(conn, "current_turn_id", None)
    if not selected_turn_id:
        return

    lock = _get_trace_lock(conn)
    store = _get_trace_store(conn)

    with lock:
        trace = store.get(selected_turn_id)
        if not trace:
            return
        elapsed_ms = (_now_perf() - trace["start_perf"]) * 1000.0
        event = {
            "stage": stage,
            "t_ms": round(elapsed_ms, 3),
            "wall_time": round(time.time(), 3),
        }
        if extra:
            event.update(extra)
        trace["events"].append(event)


def _build_stage_costs(events: list[dict[str, Any]]) -> dict[str, float]:
    start_events: dict[str, float] = {}
    durations: dict[str, float] = {}

    for ev in events:
        stage = ev.get("stage", "")
        t_ms = float(ev.get("t_ms", 0.0))
        if stage.endswith(".start"):
            key = stage[: -len(".start")]
            start_events[key] = t_ms
        elif stage.endswith(".end"):
            key = stage[: -len(".end")]
            start_t = start_events.pop(key, None)
            if start_t is not None and t_ms >= start_t:
                durations[key] = round(t_ms - start_t, 3)

    return durations


def finalize_turn(
    conn,
    status: str = "completed",
    turn_id: Optional[str] = None,
    keep_last_n: int = 50,
) -> None:
    selected_turn_id = turn_id or getattr(conn, "current_turn_id", None)
    if not selected_turn_id:
        return

    lock = _get_trace_lock(conn)
    store = _get_trace_store(conn)

    with lock:
        trace = store.get(selected_turn_id)
        if not trace or trace.get("completed"):
            return

        trace["completed"] = True
        events = trace.get("events", [])
        total_ms = round((_now_perf() - trace["start_perf"]) * 1000.0, 3)

        first_audio_ms = None
        for ev in events:
            if ev.get("stage") == "tts.first_audio_sent":
                first_audio_ms = float(ev.get("t_ms", 0.0))
                break

        summary = {
            "turn_id": trace.get("turn_id"),
            "session_id": trace.get("session_id"),
            "source": trace.get("source"),
            "text": trace.get("text"),
            "total_ms": total_ms,
            "first_audio_ms": first_audio_ms,
            "stages": _build_stage_costs(events),
            "event_count": len(events),
            "status": status,
        }

        # 清理过旧的trace，防止内存增长
        if len(store) > keep_last_n:
            items = sorted(
                store.items(),
                key=lambda kv: kv[1].get("created_at", 0),
            )
            for old_turn_id, _ in items[: len(store) - keep_last_n]:
                if old_turn_id != selected_turn_id:
                    store.pop(old_turn_id, None)

    try:
        conn.logger.bind(tag=TAG).info(f"LATENCY_TRACE {json.dumps(summary, ensure_ascii=False)}")
    except Exception:
        pass
