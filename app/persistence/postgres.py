"""PostgreSQL 持久化（真实部署版，对应第十节状态持久化 + Checkpoint）。

默认项目用本地 JSON 持久化（app/persistence），保证零依赖可跑通。
真实部署把 Task/Trace/Artifact 落 Postgres，并用
langgraph-checkpoint-postgres 做 LangGraph Checkpoint。

用法：先建库，再执行本文件的 SCHEMA，或直接 `python -m app.persistence.postgres`。
"""

from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id        TEXT PRIMARY KEY,
  user_id        TEXT,
  instruction    TEXT,
  deliverable    TEXT DEFAULT 'markdown',
  status         TEXT,
  plan           JSONB,
  retry_counts   JSONB,
  replan_count   INT DEFAULT 0,
  pending_approval JSONB,
  final_result   JSONB,
  created_at     DOUBLE PRECISION,
  updated_at     DOUBLE PRECISION,
  meta           JSONB
);

CREATE TABLE IF NOT EXISTS artifacts (
  result_id     TEXT PRIMARY KEY,
  task_id       TEXT,
  step_id       TEXT,
  artifact_type TEXT,
  storage_path  TEXT,
  summary       TEXT,
  key_entities  JSONB,
  metadata      JSONB,
  created_at    DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS trace_events (
  id            BIGSERIAL PRIMARY KEY,
  trace_id      TEXT,
  task_id       TEXT,
  step_id       TEXT,
  agent_name    TEXT,
  event_type    TEXT,
  input         JSONB,
  output        JSONB,
  tool_name     TEXT,
  tool_args     JSONB,
  tool_result   JSONB,
  status        TEXT,
  latency_ms    INT,
  token_usage   JSONB,
  error         TEXT,
  retry_count   INT,
  checkpoint_id TEXT,
  timestamp     DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
CREATE INDEX IF NOT EXISTS idx_trace_task ON trace_events(task_id);
"""


def init_db(dsn: str) -> None:
    """执行 SCHEMA。dsn 形如 postgresql+psycopg://...（去 schema 驱动前缀）。"""
    import psycopg
    clean = dsn.split("+")[-1]
    with psycopg.connect(clean) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()


def main() -> None:
    from ..config import get_settings
    s = get_settings()
    dsn = f"postgresql://{s.pg_user}:{s.pg_password}@{s.pg_host}:{s.pg_port}/{s.pg_db}"
    init_db(dsn)
    print(f"PostgreSQL schema initialized at {s.pg_db}")


if __name__ == "__main__":
    main()
