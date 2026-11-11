"""Private, unauthenticated lesson API: must remain loopback + SSH only."""

from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from task_service import database
from task_service.store import DatabaseFailure, MySQLStore, TaskMissing


ORIGIN = "http://127.0.0.1:18016"
HOST = "127.0.0.1:18016"
MAX_ID = 2**64 - 1
TaskId = Annotated[int, Path(ge=1, le=MAX_ID)]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(strict=True, min_length=1, max_length=200)


class TaskRead(BaseModel):
    id: int = Field(ge=1, le=MAX_ID)
    run_key: UUID
    title: str
    status: Literal["pending", "cancelled"]
    event_count: int = Field(ge=0)


class EventRead(BaseModel):
    id: int = Field(ge=1, le=MAX_ID)
    task_id: int = Field(ge=1, le=MAX_ID)
    kind: Literal["created", "cancelled"]


class EventPage(BaseModel):
    items: list[EventRead]
    after_id: int
    limit: int
    next_after_id: int


def build_store():
    config = database.load_settings()
    return MySQLStore(lambda: database.open_connection(config))


def create_app(store=None):
    @asynccontextmanager
    async def lifespan(app):
        # No connection is held across requests. No network or table creation
        # at import/factory time; mounted configuration is read at startup.
        app.state.store = store if store is not None else build_store()
        try:
            yield
        finally:
            app.state.store = None

    app = FastAPI(title="Task Service API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.headers.get("host", "").lower() != HOST:
            result = JSONResponse(status_code=400, content={"detail": "Unexpected host"})
        elif request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("origin") != ORIGIN:
            result = JSONResponse(status_code=403, content={"detail": "Same-origin request required"})
        elif (request.method in {"POST", "PUT", "PATCH", "DELETE"}
              and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"):
            result = JSONResponse(status_code=415, content={"detail": "Use application/json"})
        else:
            result = await call_next(request)
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        return result

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(item["loc"]), "type": item["type"], "msg": "Invalid request field"}
            for item in error.errors()
        ]})

    @app.exception_handler(TaskMissing)
    async def missing_task(request, error):
        return JSONResponse(status_code=404, content={"detail": "Task not found"})

    @app.exception_handler(DatabaseFailure)
    async def failed_database(request, error):
        return JSONResponse(status_code=503, content={"detail": "Database operation failed"})

    @app.get("/health")
    def health():
        return {"status": "ok", "storage": "mysql", "mode": "local-demo"}

    @app.get("/ready")
    def ready(request: Request):
        return request.app.state.store.ready()

    @app.post("/tasks", response_model=TaskRead, status_code=201)
    def create_task(request: Request, body: TaskCreate):
        return request.app.state.store.create(body.title)

    @app.get("/tasks/{task_id}", response_model=TaskRead)
    def get_task(request: Request, task_id: TaskId):
        return request.app.state.store.get(task_id)

    @app.get("/tasks/{task_id}/events", response_model=EventPage)
    def get_events(
        request: Request, task_id: TaskId,
        after_id: Annotated[int, Query(ge=0, le=MAX_ID)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        return request.app.state.store.events(task_id, after_id, limit)

    return app
