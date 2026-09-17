"""任务状态持久化 + 重启恢复 + Checkpoint（第十节）。

默认用本地 JSON（data/tasks/{task_id}.json）落 TaskRecord，实现：
  - 状态持久化
  - 服务重启后恢复未完成任务
  - Checkpoint（每个大状态跃迁写一次）
  - 幂等执行（恢复时跳过已执行步骤）

真实部署可换 PostgreSQL（见 persistence_postgres.py）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ..models import TaskRecord, TaskStatus


class TaskStore:
    """任务记录的文件持久化。线程安全。"""

    # 需要 checkpoint 的"大状态"跃迁
    _CHECKPOINT_STATES = {
        TaskStatus.READY, TaskStatus.WAITING_APPROVAL,
        TaskStatus.COMPLETED, TaskStatus.HUMAN_HANDOFF,
        TaskStatus.FAILED, TaskStatus.WAITING_CLARIFICATION,
    }

    def __init__(self, base: Path | None = None):
        from ..config import get_settings
        self.dir = (base or get_settings().data_dir) / "tasks"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- 持久化 --------------------------------------------------------
    def save(self, task: TaskRecord, force: bool = False) -> None:
        with self._lock:
            task.updated_at = __import__("time").time()
            p = self.dir / f"{task.task_id}.json"
            p.write_text(task.model_dump_json(), encoding="utf-8")

    def checkpoint(self, task: TaskRecord) -> None:
        """仅在跨入检查点状态时落盘。"""
        if task.status in self._CHECKPOINT_STATES:
            self.save(task, force=True)

    def load(self, task_id: str) -> TaskRecord | None:
        p = self.dir / f"{task_id}.json"
        if not p.exists():
            return None
        return TaskRecord.model_validate_json(p.read_text(encoding="utf-8"))

    def list_tasks(self) -> list[TaskRecord]:
        out: list[TaskRecord] = []
        for p in sorted(self.dir.glob("*.json")):
            out.append(TaskRecord.model_validate_json(p.read_text(encoding="utf-8")))
        return out

    def pending_tasks(self) -> list[TaskRecord]:
        """尚未终态、可恢复继续的任务。"""
        terminal = {TaskStatus.COMPLETED, TaskStatus.FAILED,
                    TaskStatus.HUMAN_HANDOFF}
        return [t for t in self.list_tasks() if t.status not in terminal]


_store: TaskStore | None = None


def get_task_store() -> TaskStore:
    global _store
    if _store is None:
        _store = TaskStore()
    return _store
