"""Tool Registry + Tool Governance（第三节）。

本项目内部组件，非 AgentOS。每个工具保存：
name / description / input_schema / permission / risk_level / timeout / retry_policy。

执行流程：Agent → Tool Permission → Risk Check → HITL（高风险）→ Execute → Result。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import get_settings
from ..models import Approval


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    permission: str = "user"
    risk_level: str = "LOW"          # LOW / HIGH
    timeout: int = 30
    retry_policy: dict[str, Any] = field(default_factory=lambda: {"retries": 2, "backoff": 1.0})
    handler: Callable | None = None


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    latency_ms: int = 0
    retries_used: int = 0


class ToolGovernor:
    def __init__(self) -> None:
        self.s = get_settings()
        self._tools: dict[str, ToolSpec] = {}

    # -- 注册 -------------------------------------------------------------
    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    # -- 权限 / 风险 ------------------------------------------------------
    def requires_approval(self, name: str) -> bool:
        spec = self._tools.get(name)
        if spec is None:
            return True
        return spec.risk_level == "HIGH" and not self.s.approval_auto_grant

    def build_approval(self, task_id: str, step_id: str, name: str,
                       args: dict[str, Any]) -> Approval:
        spec = self._tools.get(name)
        risk = spec.risk_level if spec else "HIGH"
        return Approval(task_id=task_id, step_id=step_id, tool=name,
                       payload=args, risk_level=risk)

    # -- 执行 -------------------------------------------------------------
    def execute(self, name: str, args: dict[str, Any],
                *, task_id: str = "", step_id: str = "") -> ToolResult:
        """执行单个工具；对 HIGH 风险工具强制校验审批已发生（HITL 闭环）。"""
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(ok=False, error=f"unknown tool: {name}")
        if self.requires_approval(name):
            # 高风险必须在调用方拿到 approved 后才允许真正执行；
            # 这里通过 args 里注入的 _approved 标记做最后一道闸。
            if args.pop("_approved", False) is not True:
                return ToolResult(ok=False,
                                  error=f"tool '{name}' is HIGH risk and requires human approval before execution")
        retries = spec.retry_policy.get("retries", 0)
        backoff = spec.retry_policy.get("backoff", 1.0)
        last_err = ""
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                data = spec.handler(**args) if spec.handler else None
                latency = int((time.time() - t0) * 1000)
                return ToolResult(ok=True, data=data, latency_ms=latency,
                                  retries_used=attempt)
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                if attempt < retries:
                    time.sleep(backoff * (attempt + 1))
        latency = int((time.time() - t0) * 1000)
        return ToolResult(ok=False, error=last_err, latency_ms=latency,
                          retries_used=retries)


_governor: ToolGovernor | None = None


def get_governor() -> ToolGovernor:
    global _governor
    if _governor is None:
        _governor = ToolGovernor()
    return _governor
