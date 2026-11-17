import asyncio
import json
import unittest
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient

from app import Card, Filters, Portal, Settings, app
from rag import CircuitBreaker, Trace, check_answer, provider_failure, select_fragments


def card(visibility="cloud_ok", status="active"):
    return {
        "doc_id": "M-05", "version": "2.0", "status": status, "visibility": visibility,
        "title": "Ограничения каналов", "path": "Инженерия / Каналы",
        "anchor": "s01", "excerpt": "До 5 000 активных подписчиков.",
        "chunks": [{"anchor": "s01", "heading": "Лимит", "text": "До 5 000 активных подписчиков."}],
    }


def response(content="До 5 000 активных подписчиков [1]"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


class PortalTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, cloud, local, hit=None, private=False, key="demo-only"):
        calls = []
        replies = {"mock": cloud, "local": local}

        def dispatch(request):
            messages = json.loads(request.content)["messages"]
            calls.append((request.url.host, messages, request.headers.get("authorization")))
            return replies[request.url.host]

        async with httpx.AsyncClient(transport=httpx.MockTransport(dispatch)) as http:
            config = Settings()
            config.cloud_url = "http://mock/v1"
            config.local_url = "http://local/v1"
            config.cloud_key = key
            portal = Portal(config, http, http)
            portal.tool = AsyncMock(return_value={"index_version": "v1", "cards": [hit] if hit else []})
            trace = Trace("request-id")
            result = await portal.answer(Filters(query="Лимит подписчиков", private=private), trace)
            self.assertEqual(portal.tool.await_count, 1)
            self.assertEqual(trace.mcp_calls, 1)
            return result, calls

    async def test_cloud_success_uses_server_citation(self):
        result, calls = await self.request(response(), response(), card())
        self.assertEqual(result["route"], "cloud")
        self.assertEqual(result["sources"][0]["url"], "/docs/M-05/2.0#s01")
        self.assertEqual([host for host, *_ in calls], ["mock"])

    async def test_rate_limit_falls_back_with_same_messages(self):
        result, calls = await self.request(httpx.Response(429), response(), card())
        self.assertEqual(result["route"], "local")
        self.assertEqual(result["reason"], "cloud_rate_limit")
        self.assertEqual([host for host, *_ in calls], ["mock", "local"])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(calls[0][2], "Bearer demo-only")
        self.assertIsNone(calls[1][2])

    async def test_cloud_privacy_controls(self):
        for hit, private, key, expected in [
            (card(), True, "demo-only", "private_mode"),
            (card("local_only"), False, "demo-only", "local_only_context"),
            (card(None), False, "demo-only", "local_only_context"),
            (card(), False, "", "cloud_not_configured"),
        ]:
            with self.subTest(reason=expected):
                result, calls = await self.request(response(), response(), hit, private, key)
                self.assertEqual(result["reason"], expected)
                self.assertEqual([host for host, *_ in calls], ["local"])

    async def test_rejects_unbound_answer_and_keeps_sources(self):
        output = response("Неподтверждённый результат [9]")
        result, _ = await self.request(output, output, card())
        self.assertEqual(result["route"], "search_only")
        self.assertEqual(result["sources"][0]["doc_id"], "M-05")
        self.assertIn("Недостаточно", result["answer"])

    async def test_empty_search_avoids_models(self):
        result, calls = await self.request(response(), response())
        self.assertEqual(result["route"], "search_only")
        self.assertFalse(calls)

    async def test_circuit_breaker_allows_one_probe_and_recovers(self):
        breaker = CircuitBreaker()
        permit = await breaker.acquire()
        await breaker.finish(permit, 0)
        probe = await breaker.acquire()
        self.assertTrue(probe[1])
        self.assertIsNone(await breaker.acquire())
        await breaker.finish(probe, None)
        self.assertFalse((await breaker.acquire())[1])

    async def test_local_queue_limits_and_unblocks_after_cancellation(self):
        started = asyncio.Event()

        async def block(_request):
            started.set()
            await asyncio.Future()

        async with httpx.AsyncClient(transport=httpx.MockTransport(block)) as http:
            portal = Portal(Settings(), http, http)
            local = portal.local
            run = lambda: asyncio.create_task(local.complete(http, "http://local/v1", "model", [], 60))
            first = run()
            await started.wait()
            second = run()
            await asyncio.sleep(0)
            with self.assertRaisesRegex(Exception, "local_queue_full"):
                await local.complete(http, "http://local/v1", "model", [], 60)
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
            self.assertEqual(local.pending, 0)


class ContractTests(unittest.TestCase):
    def test_archive_is_labeled_in_answer(self):
        fragments = select_fragments([card(status="archived")], "Лимит")
        result = check_answer("До 1 000 подписчиков [1]", fragments)
        self.assertTrue(result[0].startswith("(Архив)"))
        self.assertIsNone(check_answer("Факт без ссылки.\nДругой факт [1]", fragments))
        self.assertIsNone(check_answer("Факт [1] https://example.org", fragments))

    def test_quota_and_rate_limit_have_distinct_reasons(self):
        quota = provider_failure(httpx.Response(429, json={"error": {"code": "insufficient_quota"}}))
        frequency = provider_failure(httpx.Response(429, headers={"Retry-After": "7"}))
        self.assertEqual((quota.reason, quota.cooldown), ("cloud_quota", 1800))
        self.assertEqual((frequency.reason, frequency.cooldown), ("cloud_rate_limit", 7))

    def test_portal_serves_interface_and_sanitizes_input_errors(self):
        with TestClient(app) as client:
            self.assertIn("Найти документы", client.get("/").text)
            invalid = client.post("/api/ask", json={"query": "x", "secret-text": "do-not-echo"})
            self.assertEqual(invalid.status_code, 422)
            self.assertNotIn("do-not-echo", invalid.text)
            self.assertEqual(client.get("/readyz").json(), {"status": "ready"})


if __name__ == "__main__":
    unittest.main()
