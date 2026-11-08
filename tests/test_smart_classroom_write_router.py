"""ASGI boundary regression with synthetic SQLite and an in-memory school."""
import asyncio
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from classroom_app.dependencies import get_current_user
from classroom_app.routers import smart_classroom_write as routes
from tests import test_smart_classroom_write as fixtures


class SmartWriteRouterTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.SmartWriteTests(methodName="test_handoff_diff_external_change_and_readback_conserve_all_people")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.preview=asyncio.run(self.fixture.preview())
        self.user=self.fixture.user.copy()
        patcher=patch.object(routes,"get_db_connection",self.fixture.database)
        patcher.start();self.addCleanup(patcher.stop)
        app=FastAPI();app.include_router(routes.router)

        def session():
            if self.user is None:raise HTTPException(401,"synthetic anonymous")
            return self.user

        app.dependency_overrides[get_current_user]=session
        self.client=TestClient(app);self.addCleanup(self.client.close)
        self.base="/api/classrooms/11/login-attendance/rounds/31/external"

    def test_owned_read_is_private_and_all_other_roles_or_scopes_are_denied(self):
        response=self.client.get(self.base+"/runs/"+self.preview["id"])
        self.assertEqual(200,response.status_code)
        self.assertEqual("no-store",response.headers["cache-control"])
        self.assertFalse(response.json()["full_remote_round_confirmed"])
        self.assertNotIn("credential_generation",response.text)
        self.assertNotIn("external_account_key",response.text)
        self.assertEqual(404,self.client.get(self.base.replace("classrooms/11","classrooms/12")+"/capabilities").status_code)
        for user,code in (({"role":"teacher","id":2},403),({"role":"student","id":1},403),(None,401)):
            self.user=user
            self.assertEqual(code,self.client.get(self.base+"/runs/"+self.preview["id"]).status_code)

    def test_cross_origin_and_untrusted_fields_cannot_enqueue_or_enable_writes(self):
        payload={"preview_id":self.preview["id"],"idempotency_key":"same-operation"}
        with patch("classroom_app.services.scheduled_task_service.schedule_task",return_value=1) as schedule:
            for headers in ({"Origin":"https://untrusted.example"},{"Sec-Fetch-Site":"cross-site"}):
                self.assertEqual(403,self.client.post(self.base+"/runs",json=payload,headers=headers).status_code)
            self.assertEqual(422,self.client.post(self.base+"/runs",json={**payload,"enable_write":True}).status_code)
            self.assertEqual(0,schedule.call_count)
            first=self.client.post(self.base+"/runs",json=payload,headers={"Origin":"http://testserver"})
            second=self.client.post(self.base+"/runs",json=payload)
            self.assertEqual(202,first.status_code)
            self.assertEqual(first.json(),second.json())
            self.assertEqual(1,schedule.call_count)
        capabilities=self.client.get(self.base+"/capabilities").json()
        self.assertFalse(capabilities["start"])
        self.assertFalse(capabilities["end"])
        self.assertFalse(capabilities["update_student"])

    def test_forged_source_is_rejected_before_any_remote_request(self):
        before=self.fixture.remote.await_count
        response=self.client.post(self.base+"/bindings",json={"source_token":"forged","remote_checkin_id":"remote-round"})
        self.assertEqual(409,response.status_code)
        self.assertEqual(before,self.fixture.remote.await_count)

    def test_binding_reload_exposes_only_public_identity_and_real_revision(self):
        response=self.client.get(self.base+"/bindings")
        self.assertEqual(200,response.status_code)
        self.assertEqual("no-store",response.headers["cache-control"])
        row=response.json()["items"][0]
        self.assertEqual(self.preview["binding_id"],row["id"])
        self.assertEqual(1,row["revision"])
        for secret in ("credential_generation","credential_id","external_account_key","source_token","source_json","remote_snapshot_json"):
            self.assertNotIn(secret,response.text)
        self.user={"role":"teacher","id":2}
        self.assertEqual(403,self.client.get(self.base+"/bindings").status_code)


if __name__=="__main__":unittest.main()
