"""Artifact Store（第五节）：统一结果外置存储。

Agent 默认只拿 summary + result_id；需要完整内容时再按 result_id 取回。
默认落本地文件系统，并维护一份内存索引（供 Reporter/Evaluator 快速查询）。
PostgreSQL 中另行持久化元数据（见 persistence 模块）。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from ..config import ARTIFACT_DIR, get_settings
from ..models import Artifact, ArtifactType, ResultReference


class ArtifactStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.root = ARTIFACT_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, Artifact] = {}

    # -- write -------------------------------------------------------------
    def save_text(self, task_id: str, step_id: str, content: str,
                  artifact_type: ArtifactType, summary: str = "",
                  key_entities: list[str] | None = None,
                  metadata: dict | None = None, ext: str = "md") -> Artifact:
        art = Artifact.new(task_id, artifact_type, storage_path="", summary=summary,
                           step_id=step_id, key_entities=key_entities or [],
                           metadata=metadata or {})
        path = self._path_for(art.task_id, ext, art.result_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        art.storage_path = str(path)
        self._index[art.result_id] = art
        return art

    def save_bytes(self, task_id: str, step_id: str, data: bytes,
                   artifact_type: ArtifactType, summary: str = "",
                   key_entities: list[str] | None = None,
                   metadata: dict | None = None, ext: str = "bin") -> Artifact:
        art = Artifact.new(task_id, artifact_type, storage_path="", summary=summary,
                           step_id=step_id, key_entities=key_entities or [],
                           metadata=metadata or {})
        path = self._path_for(art.task_id, ext, art.result_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        art.storage_path = str(path)
        self._index[art.result_id] = art
        return art

    def record_reference(self, result_id: str, summary: str,
                         key_entities: list[str]) -> ResultReference:
        return ResultReference(result_id=result_id, summary=summary,
                               key_entities=key_entities)

    # -- read --------------------------------------------------------------
    def _path_for(self, task_id: str, ext: str, result_id: str) -> Path:
        return self.root / task_id / f"{result_id}.{ext}"

    def get(self, result_id: str) -> Artifact | None:
        return self._index.get(result_id)

    def load_content(self, result_id: str) -> str | bytes | None:
        art = self._index.get(result_id)
        if art is None:
            return None
        p = Path(art.storage_path)
        if not p.exists():
            return None
        if p.suffix in {".bin", ".pptx", ".xlsx", ".ppt", ".pdf", ".docx"}:
            return p.read_bytes()
        return p.read_text(encoding="utf-8")

    def list_for_task(self, task_id: str) -> list[Artifact]:
        return [a for a in self._index.values() if a.task_id == task_id]

    def all_summaries(self, task_id: str) -> list[ResultReference]:
        """供 Reporter 汇总：只传摘要，不传原始内容。"""
        out = []
        for a in self.list_for_task(task_id):
            out.append(self.record_reference(a.result_id, a.summary, a.key_entities))
        return out

    def task_dir(self, task_id: str) -> Path:
        return self.root / task_id

    # -- persistence (metadata to a sidecar json so restarts can rebuild) ---
    def flush_meta(self) -> None:
        meta = self.root / "_index.json"
        meta.write_text(json.dumps(
            {k: v.model_dump() for k, v in self._index.items()},
            ensure_ascii=False, indent=2), encoding="utf-8")

    def reload_meta(self) -> None:
        meta = self.root / "_index.json"
        if meta.exists():
            data = json.loads(meta.read_text(encoding="utf-8"))
            self._index = {k: Artifact.model_validate(v) for k, v in data.items()}


_store: ArtifactStore | None = None


def get_artifact_store() -> ArtifactStore:
    global _store
    if _store is None:
        _store = ArtifactStore()
        _store.reload_meta()
    return _store
