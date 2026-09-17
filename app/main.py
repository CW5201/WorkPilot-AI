"""FastAPI 应用入口（企业办公数字员工 Multi-Agent）。

运行：
  uvicorn app.main:app --reload --port 8000
启动即初始化 Tool Registry；任务在后台线程异步执行（长任务不阻塞请求）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router as tasks_router
from .tools.registry import ensure_tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_tools()  # 注册全部工具
    yield


def create_app() -> FastAPI:
    from .config import get_settings
    s = get_settings()
    app = FastAPI(title=s.app_name, version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"],
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(tasks_router)

    @app.get("/", tags=["meta"])
    def root():
        return {"app": s.app_name, "docs": "/docs", "tasks": "/tasks"}

    @app.get("/eval/run", tags=["eval"])
    def eval_run(n: int = 5) -> dict:
        from .evaluation import run_benchmark
        return run_benchmark(n=n)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
