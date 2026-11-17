import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from time import monotonic
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field

from rag import (
    CircuitBreaker, LocalQueue, ProviderFailure, Trace,
    check_answer, complete, elapsed, messages_for, select_fragments,
)

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("uvicorn.error")
for library in ("mcp", "httpx", "httpcore"):
    logging.getLogger(library).setLevel(logging.CRITICAL)


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=2, max_length=500)
    path: str | None = Field(default=None, max_length=200)
    version: str | None = Field(default=None, pattern=r"^\d+(\.\d+)+$")
    status: str | None = Field(default=None, pattern="^(active|archived|draft)$")
    include_archive: bool = False
    include_drafts: bool = False
    private: bool = False

    def search_args(self) -> dict:
        return {**self.model_dump(exclude={"private"}, exclude_none=True), "limit": 5}


class Chunk(BaseModel):
    anchor: str = Field(pattern=r"^s\d+$")
    heading: str
    text: str


class Card(BaseModel):
    doc_id: str = Field(pattern=r"^[A-Z]+-\d+$")
    version: str = Field(pattern=r"^\d+(\.\d+)+$")
    status: str = Field(pattern="^(active|archived|draft)$")
    visibility: str | None = None
    title: str
    path: str
    anchor: str = Field(pattern=r"^s\d+$")
    excerpt: str
    chunks: list[Chunk]


class SearchResult(BaseModel):
    index_version: str
    cards: list[Card] = Field(max_length=5)


class DocRef(BaseModel):
    doc_id: str = Field(pattern=r"^[A-Z]+-\d+$")
    version: str = Field(pattern=r"^\d+(\.\d+)+$")
    anchor: str | None = Field(default=None, pattern=r"^s\d+$")


class Document(DocRef):
    status: str = Field(pattern="^(active|archived|draft)$")
    visibility: str
    title: str
    path: str
    date: str
    replaces: str | None = None
    sections: list[Chunk]
    links: list[DocRef] = Field(default_factory=list)


class ToolError(Exception):
    pass


class Settings:
    def __init__(self):
        self.mcp_url = os.getenv("MCP_URL", "http://localhost:8001/mcp")
        self.mcp_token = os.getenv("MCP_TOKEN", "")
        self.cloud_url = os.getenv("CLOUD_BASE_URL", "https://api.llm7.io/v1")
        self.cloud_model = os.getenv("CLOUD_MODEL", "default")
        self.cloud_key = os.getenv("LLM7_API_KEY", "")
        self.local_url = os.getenv("LOCAL_BASE_URL", "http://localhost:11434/v1")
        self.local_model = os.getenv("LOCAL_MODEL", "qwen2.5:1.5b-instruct-q4_K_M")
        self.mcp_timeout = float(os.getenv("MCP_TIMEOUT", "2"))
        self.cloud_timeout = float(os.getenv("CLOUD_TIMEOUT", "8"))
        self.local_timeout = float(os.getenv("LOCAL_TIMEOUT", "75"))
        for url in (self.mcp_url, self.cloud_url, self.local_url):
            if httpx.URL(url).scheme not in {"http", "https"} or not httpx.URL(url).host:
                raise ValueError("Invalid service URL")
        if not all(0 < value < 90 for value in (self.mcp_timeout, self.cloud_timeout, self.local_timeout)):
            raise ValueError("Invalid service timeout")


class Portal:
    def __init__(self, settings: Settings, mcp_http: httpx.AsyncClient, model_http: httpx.AsyncClient):
        self.config = settings
        self.mcp_http = mcp_http
        self.model_http = model_http
        self.cloud = CircuitBreaker()
        self.local = LocalQueue()

    async def tool(self, name: str, args: dict) -> dict:
        try:
            async with asyncio.timeout(self.config.mcp_timeout):
                async with streamable_http_client(self.config.mcp_url, http_client=self.mcp_http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(name, args)
        except Exception:
            raise HTTPException(503, "База знаний временно недоступна.") from None
        if result.isError:
            raise ToolError(name)
        if not isinstance(result.structuredContent, dict):
            raise HTTPException(503, "Ответ базы знаний не соответствует схеме.")
        return result.structuredContent

    async def search(self, filters: Filters, trace: Trace) -> SearchResult:
        started = monotonic()
        trace.mcp_calls += 1
        try:
            return SearchResult.model_validate(await self.tool("search_docs", filters.search_args()))
        except Exception:
            trace.reason = "mcp_error"
            raise HTTPException(503, "Поиск по базе знаний временно недоступен.") from None
        finally:
            trace.mcp_ms += elapsed(started)

    async def answer(self, filters: Filters, trace: Trace) -> dict:
        result = await self.search(filters, trace)
        cards = [card.model_dump() for card in result.cards]
        fragments = select_fragments(cards, filters.query)
        reply = {
            "request_id": trace.request_id,
            "index_version": result.index_version,
            "cards": cards,
            "answer": "Недостаточно подтверждённых сведений для ответа. Можно уточнить вопрос или открыть найденные документы.",
            "sources": [fragment.source() for fragment in fragments],
        }
        if fragments:
            answer = await self.generate(filters, cards, fragments, trace)
            if answer:
                reply["answer"], reply["sources"] = answer
        return {**reply, "route": trace.route, "reason": trace.reason}

    async def generate(self, filters, cards, fragments, trace):
        messages = messages_for(filters.query, fragments)
        config = self.config
        if filters.private:
            trace.reason = "private_mode"
        elif any(card["visibility"] != "cloud_ok" for card in cards):
            trace.reason = "local_only_context"
        elif not config.cloud_key:
            trace.reason = "cloud_not_configured"
        else:
            permit = await self.cloud.acquire()
            if permit is None:
                trace.reason = "cloud_cooldown"
            else:
                started = monotonic()
                trace.cloud_calls += 1
                cooldown = None
                try:
                    text = await complete(
                        self.model_http, config.cloud_url, config.cloud_model, messages,
                        config.cloud_timeout, config.cloud_key,
                    )
                    checked = check_answer(text, fragments)
                    if checked:
                        trace.route, trace.reason = "cloud", "cloud_ok"
                        return checked
                    trace.reason = "cloud_unsupported_answer"
                except ProviderFailure as error:
                    trace.reason, cooldown = error.reason, error.cooldown
                finally:
                    trace.cloud_ms = elapsed(started)
                    await self.cloud.finish(permit, cooldown)

        started = monotonic()
        trace.local_calls += 1
        try:
            text = await self.local.complete(
                self.model_http, config.local_url, config.local_model,
                messages_for(filters.query, fragments, select_only=True), config.local_timeout,
            )
            checked = check_answer(text, fragments, extractive=True)
            if checked:
                trace.route = "local"
                return checked
            trace.reason += "+local_unsupported_answer"
        except ProviderFailure as error:
            trace.reason += "+" + error.reason.replace("cloud_", "local_", 1)
            if error.reason == "local_queue_full":
                trace.local_calls -= 1
        finally:
            trace.local_ms = elapsed(started)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Settings()
    headers = {"Authorization": f"Bearer {config.mcp_token}"} if config.mcp_token else {}
    async with httpx.AsyncClient(headers=headers, follow_redirects=False, trust_env=False) as mcp_http:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as model_http:
            app.state.portal = Portal(config, mcp_http, model_http)
            yield


app = FastAPI(title="ЛираТех · База знаний", lifespan=lifespan, docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=BASE / "templates")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, _error):
    return JSONResponse({"detail": "Проверьте вопрос и значения фильтров."}, status_code=422)


@app.get("/health")
@app.get("/readyz")
async def health():
    return {"status": "ready"}


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/docs/{doc_id}/{version}")
async def doc(request: Request, doc_id: str, version: str):
    if len(doc_id) > 32 or len(version) > 32:
        raise HTTPException(404, "Редакция не найдена.")
    try:
        raw = await request.app.state.portal.tool("get_doc", {"doc_id": doc_id, "version": version})
    except ToolError:
        raise HTTPException(404, "Редакция не найдена.") from None
    return templates.TemplateResponse(request, "doc.html", {"doc": Document.model_validate(raw)})


@app.get("/api/sections")
async def sections(request: Request, include_archive: bool = False, include_drafts: bool = False):
    try:
        return await request.app.state.portal.tool(
            "list_sections", {"include_archive": include_archive, "include_drafts": include_drafts},
        )
    except ToolError:
        raise HTTPException(503, "Дерево документов временно недоступно.") from None


@app.post("/api/search")
async def search(request: Request, filters: Filters):
    trace = Trace(str(uuid4()))
    try:
        result = await request.app.state.portal.search(filters, trace)
        trace.reason = "search_requested"
        return {**result.model_dump(), "request_id": trace.request_id, "route": trace.route}
    finally:
        LOG.info(json.dumps(trace.record(), separators=(",", ":")))


async def until_disconnect(request: Request):
    while True:
        if (await request.receive())["type"] == "http.disconnect":
            return


@app.post("/api/ask")
async def ask(request: Request, filters: Filters):
    trace = Trace(str(uuid4()))
    answer_task = asyncio.create_task(request.app.state.portal.answer(filters, trace))
    disconnect_task = asyncio.create_task(until_disconnect(request))
    try:
        done, _ = await asyncio.wait({answer_task, disconnect_task}, return_when=asyncio.FIRST_COMPLETED)
        if answer_task in done:
            return answer_task.result()
        trace.reason = "client_disconnected"
        return JSONResponse({}, status_code=499)
    finally:
        for task in (answer_task, disconnect_task):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        LOG.info(json.dumps(trace.record(), separators=(",", ":")))
