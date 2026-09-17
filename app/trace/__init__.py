"""Trace（第十二节）：完整执行轨迹记录。

记录字段：trace_id/task_id/step_id/agent_name/event_type/input/output/
tool_name/tool_args/tool_result/status/latency_ms/token_usage/error/
retry_count/checkpoint_id/timestamp。

Agent 侧 append()；前端读 TaskRecord 里挂着的 trace 列表做 Timeline。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..config import get_settings
from ..models import TraceEvent


class Tracer:
    def __init__(self, task_id: str, trace_id: str | None = None) -> None:
        self.task_id = task_id
        self.trace_id = trace_id or f"trace_{uuid.uuid4().hex[:10]}"
        self.events: list[TraceEvent] = []
        self._sink: Path | None = None
        s = get_settings()
        self._sink = s.data_dir / "traces" / f"{task_id}.jsonl"

    def log(self, agent_name: str, event_type: str, *, step_id: str = "",
            input_=None, output=None, tool_name: str = "", tool_args=None,
            tool_result=None, status: str = "OK", latency_ms: int = 0,
            token_usage=None, error: str = "", retry_count: int = 0,
            checkpoint_id: str = "") -> TraceEvent:
        ev = TraceEvent(
            trace_id=self.trace_id, task_id=self.task_id, step_id=step_id,
            agent_name=agent_name, event_type=event_type, input=input_,
            output=output, tool_name=tool_name, tool_args=tool_args or {},
            tool_result=tool_result, status=status, latency_ms=latency_ms,
            token_usage=token_usage or {}, error=error, retry_count=retry_count,
            checkpoint_id=checkpoint_id)
        self.events.append(ev)
        self._write(ev)
        return ev

    def _write(self, ev: TraceEvent) -> None:
        if self._sink is None:
            return
        self._sink.parent.mkdir(parents=True, exist_ok=True)
        with self._sink.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev.model_dump(), ensure_ascii=False, default=str) + "\n")

    def timeline(self) -> list[dict]:
        """前端 Timeline 用：按时间排序的轻量事件。"""
        return [{
            "ts": e.timestamp, "agent": e.agent_name, "event": e.event_type,
            "tool": e.tool_name, "step": e.step_id, "status": e.status,
            "latency_ms": e.latency_ms, "tokens": e.token_usage.get("total", 0),
            "retry": e.retry_count, "error": e.error,
        } for e in sorted(self.events, key=lambda x: x.timestamp)]

    def load_events(self) -> list[TraceEvent]:
        if self._sink and self._sink.exists():
            out = []
            for line in self._sink.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(TraceEvent.model_validate(json.loads(line)))
            return out
        return []


__all__ = ["Tracer", "TraceEvent"]
