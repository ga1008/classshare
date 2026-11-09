"""No network: actual billing + mock HTTP transport + durable provenance."""
import asyncio
import uuid
from unittest.mock import patch

import httpx

from classroom_app.services.ai_budget_client import AIBudgetAwareClient
from classroom_app.services.ai_usage_budget_service import AIUsageBudgetError
from classroom_app.services import ai_durable_job_service as jobs
from classroom_app.services import wanderer_ai_attribution as attribution
from classroom_app.services import ai_gateway_service as gateway
from classroom_app.services.wanderer_resource_service import snapshot
from tests.wanderer_test_support import WandererDbTestCase


class WandererPlatformAITest(WandererDbTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            row = dict(conn.execute("SELECT * FROM wanderer_agents ORDER BY id LIMIT 1").fetchone())
            conn.execute("UPDATE wanderer_agents SET status='active',daily_wake_limit=100 WHERE id=?", (row["id"],))
            conn.commit()
        self.source = attribution.AIAttribution(row["id"], row["student_id"], row["life_epoch"], row["authority_version"], str(uuid.uuid4()), self.connect)
        self.sent = []

    def used(self):
        with self.connect() as conn:
            return snapshot(conn, self.source.wanderer_id, "ai_wake")["used"]

    async def request(self, client, text="test", logical_id=None):
        if logical_id is None:
            return await client.post("/api/ai/chat", json={"new_message": text})
        return await gateway.ai_gateway_post(client, "/api/ai/chat", json_payload={"new_message": text}, task_type="test", logical_call_id=logical_id)

    def transport(self, request):
        self.sent.append(request)
        return httpx.Response(200, json={"response_text": "synthetic model response"})

    def test_direct_client_and_existing_gateway_bill_once_each_without_recursion(self):
        before = self.used()
        async def run():
            async with AIBudgetAwareClient(base_url="https://synthetic.invalid", transport=httpx.MockTransport(self.transport)) as client:
                marker = attribution._attribution.set(self.source)
                try:
                    await self.request(client)
                    await self.request(client, "second", "native-round-2")
                    await self.request(client, "second", "native-round-2")
                    await asyncio.create_task(self.request(client, "third"))
                finally:
                    attribution._attribution.reset(marker)
        asyncio.run(run())
        self.assertEqual(len(self.sent), 4)
        self.assertEqual(self.used() - before, 4)

    def test_restored_job_retry_requires_another_credit_but_reservation_retry_does_not(self):
        from dataclasses import replace
        before = self.used()
        original = replace(self.source, job_id=779)
        recovered = replace(self.source, job_id=779)
        first = original.next_call('/api/ai/chat', {'input': 'same'}, 'logical-step')
        second = recovered.next_call('/api/ai/chat', {'input': 'same'}, 'logical-step')
        self.assertNotEqual(first, second)
        attribution.reserve_call(original, first)
        attribution.reserve_call(original, first)
        attribution.reserve_call(recovered, second)
        self.assertEqual(self.used() - before, 2)

    def test_human_client_does_not_enter_wanderer_gateway(self):
        async def run():
            async with AIBudgetAwareClient(base_url="https://synthetic.invalid", transport=httpx.MockTransport(self.transport)) as client:
                with patch.object(gateway, "ai_gateway_post", side_effect=AssertionError("human path changed")):
                    await self.request(client)
        asyncio.run(run())
        self.assertEqual(len(self.sent), 1)

    def test_durable_business_write_requires_original_live_epoch_but_no_old_turn(self):
        from dataclasses import replace
        source = replace(self.source, job_id=777)
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS synthetic_ai_delivery(id INTEGER PRIMARY KEY,value TEXT)")
            conn.execute("DELETE FROM synthetic_ai_delivery")
            conn.commit()
        marker = attribution._attribution.set(source)
        try:
            with self.connect() as conn:
                conn.execute("INSERT INTO synthetic_ai_delivery VALUES(1,'accepted durable result')")
                conn.commit()  # No runtime turn/lease is required by accepted jobs.
            from classroom_app.db.service_execution_guard import suspend_service_guard
            with suspend_service_guard(), self.connect() as conn:
                conn.execute("UPDATE wanderer_agents SET life_epoch=life_epoch+1 WHERE id=?", (source.wanderer_id,))
                conn.commit()
            with self.assertRaises(AIUsageBudgetError):
                with self.connect() as conn:
                    conn.execute("INSERT INTO synthetic_ai_delivery VALUES(2,'stale result')")
                    conn.commit()
        finally:
            attribution._attribution.reset(marker)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_ai_delivery").fetchone()[0], 1)

    def test_savepoint_rollback_and_sql_commit_do_not_bypass_epoch_fence(self):
        from dataclasses import replace
        source = replace(self.source, job_id=778)
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS synthetic_ai_savepoint(id INTEGER PRIMARY KEY)")
            conn.execute("DELETE FROM synthetic_ai_savepoint")
            conn.commit()
        marker = attribution._attribution.set(source)
        try:
            with self.assertRaises(AIUsageBudgetError):
                with self.connect() as conn:
                    conn.execute("SAVEPOINT native_attempt")
                    conn.execute("INSERT INTO synthetic_ai_savepoint VALUES(1)")
                    conn.execute("ROLLBACK TO SAVEPOINT native_attempt")
                    from classroom_app.db.service_execution_guard import suspend_service_guard
                    with suspend_service_guard():
                        conn.execute("UPDATE wanderer_agents SET life_epoch=life_epoch+1 WHERE id=?", (source.wanderer_id,))
                    conn.execute("COMMIT")
        finally:
            attribution._attribution.reset(marker)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_ai_savepoint").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT life_epoch FROM wanderer_agents WHERE id=?", (source.wanderer_id,)).fetchone()[0], source.life_epoch)

    def test_stream_transport_is_billed_before_network(self):
        before = self.used()
        async def run():
            async with AIBudgetAwareClient(base_url="https://synthetic.invalid", transport=httpx.MockTransport(self.transport)) as client:
                marker = attribution._attribution.set(self.source)
                try:
                    async with client.stream("POST", "/api/ai/chat", json={"new_message": "stream"}) as response:
                        await response.aread()
                finally:
                    attribution._attribution.reset(marker)
        asyncio.run(run())
        self.assertEqual(self.used() - before, 1)
        self.assertEqual(len(self.sent), 1)

    def test_durable_job_restores_billing_after_context_loss_and_epoch_change_denies_dispatch(self):
        marker = attribution._attribution.set(self.source)
        try:
            with self.connect() as conn:
                job, _ = jobs.create_ai_job(conn, task_type="synthetic", dedupe_key=uuid.uuid4().hex,
                    payload={"fake_attribution": "not trusted"}, owner_role="student", owner_user_pk=self.source.student_id)
                conn.commit()
        finally:
            attribution._attribution.reset(marker)
        before = self.used()
        async def run():
            async with AIBudgetAwareClient(base_url="https://synthetic.invalid", transport=httpx.MockTransport(self.transport)) as client:
                for _ in range(2):
                    with attribution.restored_job_attribution(job, self.connect):
                        await self.request(client)
        asyncio.run(run())
        self.assertEqual(self.used() - before, 2)
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET life_epoch=life_epoch+1 WHERE id=?", (self.source.wanderer_id,))
            conn.commit()
        with self.assertRaises(AIUsageBudgetError):
            asyncio.run(run())
        self.assertEqual(len(self.sent), 2)

    def test_same_logical_call_cannot_resend_after_last_credit(self):
        before = self.used()
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET daily_wake_limit=? WHERE id=?", (int(before)+1, self.source.wanderer_id))
            conn.commit()
        async def run():
            async with AIBudgetAwareClient(base_url="https://synthetic.invalid", transport=httpx.MockTransport(self.transport)) as client:
                marker = attribution._attribution.set(self.source)
                try:
                    await self.request(client, logical_id='last-credit')
                    with self.assertRaises(AIUsageBudgetError):
                        await self.request(client, logical_id='last-credit')
                finally:
                    attribution._attribution.reset(marker)
        asyncio.run(run())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.used() - before, 1)

    def test_exhausted_account_never_reaches_transport(self):
        with self.connect() as conn:
            conn.execute("UPDATE wanderer_agents SET daily_wake_limit=0 WHERE id=?", (self.source.wanderer_id,))
            conn.commit()
        async def run():
            async with AIBudgetAwareClient(base_url="https://synthetic.invalid", transport=httpx.MockTransport(self.transport)) as client:
                marker = attribution._attribution.set(self.source)
                try:
                    await self.request(client)
                finally:
                    attribution._attribution.reset(marker)
        with self.assertRaises(AIUsageBudgetError):
            asyncio.run(run())
        self.assertEqual(self.sent, [])
