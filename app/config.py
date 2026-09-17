"""配置加载（pydantic-settings）。

所有外部连接通过环境变量注入，密钥不落盘、不硬编码。
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARTIFACT_DIR = DATA_DIR / "artifacts"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
TEMPLATE_DIR = DATA_DIR / "templates"
TASKSET_DIR = DATA_DIR / "tasks"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 应用
    app_name: str = "WorkPilot 企业办公数字员工"
    debug: bool = False

    # LLM（OpenAI 兼容；base_url 可指向任何兼容端点，如 vLLM / DashScope / OpenAI）
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.2
    llm_timeout: int = 120

    # LLM 降级：无 API key 时使用规则式 Planner/Reporter（离线可跑通 demo）
    llm_fallback_rule_based: bool = True

    # PostgreSQL
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_user: str = "workpilot"
    pg_password: str = "workpilot"
    pg_db: str = "workpilot"

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    # Redis
    redis_url: str = "redis://127.0.0.1:6379/0"

    # Milvus
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "workpilot_knowledge"
    milvus_dim: int = 1024  # BGE-M3 输出维度
    milvus_top_k: int = 8

    # 检索
    retrieval_enable: bool = True
    retrieval_min_relevance: float = 0.35  # 低于该阈值的检索结果判定为"无需检索"
    bm25_top_k: int = 20
    hybrid_rrf_k: int = 60

    # HITL
    approval_auto_grant: bool = False  # 演示可关闭；生产保持 False
    approval_timeout_sec: int = 300

    # 重试/重规划
    retry_step_limit: int = 2
    replan_limit: int = 2

    # 工具
    tool_default_timeout: int = 30
    tool_default_retries: int = 2

    # 数据目录（允许被 env 覆盖）
    data_dir: Path = Field(default_factory=lambda: DATA_DIR)

    @classmethod
    def load(cls) -> "Settings":
        return cls()


def get_settings() -> Settings:
    return Settings.load()


__all__ = ["Settings", "get_settings", "PROJECT_ROOT", "DATA_DIR",
            "ARTIFACT_DIR", "KNOWLEDGE_DIR", "TEMPLATE_DIR", "TASKSET_DIR"]
