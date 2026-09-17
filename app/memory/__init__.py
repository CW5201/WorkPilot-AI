"""Memory（第十一节）：Session / User / Task / Enterprise Knowledge。

不把所有聊天历史塞进 Prompt；按作用域分四类，按需取用：
  - Session Memory：当前任务正在处理什么（随任务生命周期）。
  - User Memory：用户常用模板、写作习惯、默认配置。
  - Task Memory：某任务已执行过哪些步骤（幂等/恢复用）。
  - Enterprise Knowledge：企业制度 / SOP / 模板 / 业务资料（对接 Retriever 的 data/knowledge）。
默认本地文件 + 内存索引实现；接口预留，可换 Redis/PG。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import get_settings


class Memory:
    def __init__(self) -> None:
        s = get_settings()
        self.dir = s.data_dir / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.user_dir = self.dir / "users"
        self.user_dir.mkdir(parents=True, exist_ok=True)

    # ---- User Memory -----------------------------------------------------
    def user_get(self, user_id: str) -> dict:
        p = self.user_dir / f"{user_id}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {"user_id": user_id, "prefs": {}, "created_at": time.time()}

    def user_update(self, user_id: str, prefs: dict) -> None:
        p = self.user_dir / f"{user_id}.json"
        cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() \
            else {"user_id": user_id, "prefs": {}}
        cur["prefs"].update(prefs)
        p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- Task Memory ------------------------------------------------------
    def task_done_steps(self, task_id: str) -> list[str]:
        p = self.dir / f"task_{task_id}.json"
        if not p.exists():
            return []
        return json.loads(p.read_text(encoding="utf-8")).get("done", [])

    def task_mark_done(self, task_id: str, step_id: str) -> None:
        p = self.dir / f"task_{task_id}.json"
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        done = data.get("done", [])
        if step_id not in done:
            done.append(step_id)
        data["done"] = done
        data["updated_at"] = time.time()
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- Enterprise Knowledge：委托给 Retriever（data/knowledge）----------
    def enterprise_search(self, query: str, top_k: int = 5) -> list[dict]:
        from ..retrieval import retrieve
        return retrieve(query, top_k=top_k).get("results", [])


_memory: Memory | None = None


def get_memory() -> Memory:
    global _memory
    if _memory is None:
        _memory = Memory()
    return _memory
