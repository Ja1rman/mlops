import argparse
import asyncio
import json
import os

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def check(url: str) -> dict:
    token = os.getenv("MCP_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(headers=headers, timeout=8.0, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                if tools != {"search_docs", "get_doc", "list_sections"}:
                    raise RuntimeError(f"Некорректный список инструментов: {sorted(tools)}")

                async def call(name: str, arguments: dict) -> dict:
                    result = await session.call_tool(name, arguments)
                    if result.isError or result.structuredContent is None:
                        raise RuntimeError(f"Не выполнен вызов {name}")
                    return result.structuredContent

                tree = await call("list_sections", {"include_archive": True, "include_drafts": True})
                paths = [sub for topic in tree["sections"] for sub in topic["subsections"]]
                count = sum(len(sub["documents"]) for sub in paths)
                if (len(tree["sections"]), len(paths), count) != (6, 12, 36):
                    raise RuntimeError("Дерево не соответствует размерам корпуса")

                search = await call("search_docs", {"query": "Сколько активных подписчиков помещается в закрытый канал сейчас?"})
                card = search["cards"][0]
                matches = (card["doc_id"], card["version"], card["status"])
                if matches != ("M-05", "2.0", "active") or "5 000" not in card["excerpt"]:
                    raise RuntimeError("Действующий документ M-05 не найден в поиске")

                archived = await call("get_doc", {"doc_id": "M-05", "version": "1.1"})
                if archived["status"] != "archived" or "1 000" not in archived["sections"][0]["text"]:
                    raise RuntimeError("Архив M-05 не найден")
                missing = await session.call_tool("get_doc", {"doc_id": "M-05", "version": "9.9"})
                if not missing.isError:
                    raise RuntimeError("Нужна ошибка при запросе отсутствующей версии")

                return {
                    "tools": sorted(tools),
                    "sections": len(tree["sections"]),
                    "subsections": len(paths),
                    "records": count,
                    "index_version": search["index_version"],
                    "active": f'{card["doc_id"]}@{card["version"]}#{card["anchor"]}',
                    "archived": f'{archived["doc_id"]}@{archived["version"]}',
                    "missing_version_rejected": missing.isError,
                }


def main() -> None:
    parser = argparse.ArgumentParser(description="Независимая проверка инструментов MCP по HTTP")
    parser.add_argument("url", nargs="?", default=os.getenv("MCP_URL", "http://127.0.0.1:8000/mcp"))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check(args.url)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
