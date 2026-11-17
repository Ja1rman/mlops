import os
import secrets
import sqlite3
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from kb.search import Document, KnowledgeBase, Limit, Query, SearchResults, SectionTree, Status

kb = KnowledgeBase(Path(os.getenv("KB_DB", "index.sqlite")))
host = os.getenv("KB_HOST", "127.0.0.1")
port = int(os.getenv("KB_PORT", "8000"))
allowed_hosts = ["127.0.0.1", "localhost", "[::1]", "kb-mcp"]
allowed_hosts.extend(value.strip() for value in os.getenv("KB_ALLOWED_HOSTS", "").split(",") if value.strip())
mcp = FastMCP(
    "line-kb-mcp",
    host=host,
    port=port,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[*allowed_hosts, *(f"{value}:*" for value in allowed_hosts)],
        allowed_origins=["http://127.0.0.1", "http://127.0.0.1:*", "http://localhost", "http://localhost:*"],
    ),
)


@mcp.tool()
def search_docs(
    query: Query,
    path: str | None = None,
    version: str | None = None,
    status: Status | None = None,
    include_archive: bool = False,
    include_drafts: bool = False,
    limit: Limit = 5,
) -> SearchResults:
    """Поиск до пяти редакций с безопасными для RAG отрывками и метками видимости."""
    return kb.search_docs(query, path, version, status, include_archive, include_drafts, limit)


@mcp.tool()
def get_doc(doc_id: str, version: str) -> Document:
    """Открыть точную редакцию и исходные секции документа для просмотра человеком."""
    return kb.get_doc(doc_id, version)


@mcp.tool()
def list_sections(include_archive: bool = False, include_drafts: bool = False) -> SectionTree:
    """Вернуть дерево разделов, подразделов и видимых редакций."""
    return kb.list_sections(include_archive, include_drafts)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request):
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(_request):
    try:
        metadata = kb.metadata()
    except (TypeError, sqlite3.Error):
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ready", **metadata})


class MCPBearerAuth:
    def __init__(self, app, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] in {"/mcp", "/mcp/"}:
            scheme, _, token = Headers(scope=scope).get("authorization", "").partition(" ")
            if scheme.casefold() != "bearer" or not secrets.compare_digest(token.encode(), self.token):
                response = JSONResponse(
                    {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def app():
    application = mcp.streamable_http_app()
    token = os.getenv("MCP_TOKEN")
    if token:
        application.add_middleware(MCPBearerAuth, token=token)
    return application


if __name__ == "__main__":
    uvicorn.run(app(), host=host, port=port, access_log=False)
