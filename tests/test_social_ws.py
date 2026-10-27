"""WebSocket /ws/social smoke tests (social doc §7): auth, hello, ping/pong, pushes, typing, LAN relay."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import anyio

from starlette.websockets import WebSocketDisconnect

from classroom_app.services import social_push_service
from classroom_app.services.social_common import dm_key
from classroom_app.services.wanderer_settings_service import INTERNAL_TOKEN_HEADER, wanderer_internal_token
from tests.social_test_support import SocialDbTestCase

MAX_FRAMES = 12


def fake_verify_token(token, client_ip=None):
    """Tests use the identity itself as the cookie value; anything else is rejected."""
    if not token or ":" not in str(token):
        return None
    role, pk = str(token).split(":", 1)
    return {"id": int(pk), "role": role}


def receive_until(ws, kind: str) -> dict:
    for _ in range(MAX_FRAMES):
        frame = ws.receive_json()
        if frame.get("type") == kind:
            return frame
    raise AssertionError(f"no {kind!r} frame received")


class SocialWebSocketTest(SocialDbTestCase):
    def setUp(self):
        super().setUp()
        self.befriend(self.a, self.b)
        verify = patch("classroom_app.routers.social_ws.verify_token", side_effect=fake_verify_token)
        verify.start()
        self.addCleanup(verify.stop)

    def client_as(self, identity: str):
        """One shared event loop for HTTP calls and every socket, as in the single production worker.

        Without it each TestClient websocket runs on its own loop and cross-socket pushes never wake.
        """
        client = super().client_as(identity)
        portal_cm = anyio.from_thread.start_blocking_portal()
        client.portal = portal_cm.__enter__()
        self.addCleanup(portal_cm.__exit__, None, None, None)
        return client

    @staticmethod
    def connect_ws(client, identity: str):
        return client.websocket_connect("/ws/social", headers={"cookie": f"access_token={identity}"})

    def test_rejects_missing_session(self):
        client = self.client_as(self.a)
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with client.websocket_connect("/ws/social") as ws:
                ws.receive_json()
        self.assertEqual(ctx.exception.code, 1008)

    def test_hello_ping_message_push_typing_and_lan(self):
        client = self.client_as(self.a)
        key = dm_key(self.a, self.b)
        with self.connect_ws(client, self.a) as ws_a, self.connect_ws(client, self.b) as ws_b:
            hello_a = receive_until(ws_a, "hello")
            self.assertEqual(hello_a["me"], self.a)
            self.assertEqual(hello_a["unread_total"], 0)  # system lines never count as unread
            hello_b = receive_until(ws_b, "hello")
            self.assertEqual(hello_b["online_friends"], [self.a])
            presence = receive_until(ws_a, "presence")
            self.assertEqual((presence["identity"], presence["online"]), (self.b, True))

            ws_a.send_json({"type": "ping"})
            self.assertEqual(receive_until(ws_a, "pong"), {"type": "pong"})

            sent = client.post(f"/api/social/conversations/{key}/messages", json={"body": "实时消息"})
            self.assertEqual(sent.status_code, 200, sent.text)
            pushed = receive_until(ws_b, "message")["message"]
            self.assertEqual((pushed["body"], pushed["mine"]), ("实时消息", False))
            self.assertTrue(receive_until(ws_a, "message")["message"]["mine"])

            ws_a.send_json({"type": "typing", "conversation_key": key})
            self.assertEqual(receive_until(ws_b, "typing"),
                             {"type": "typing", "conversation_key": key, "identity": self.a})

            ws_a.send_json({"type": "lan_offer", "to": self.b, "transfer_id": 7, "payload": {"sdp": "x"}})
            offer = receive_until(ws_b, "lan_offer")
            self.assertEqual((offer["from"], offer["transfer_id"], offer["payload"]), (self.a, 7, {"sdp": "x"}))

            ws_a.send_json({"type": "focus", "conversation_key": key})
            ws_a.send_json({"type": "bogus"})
            self.assertEqual(receive_until(ws_a, "error")["detail"], "未知的消息类型。")

    def test_lan_relay_refused_for_strangers(self):
        client = self.client_as(self.c)
        with self.connect_ws(client, self.a) as ws_a, self.connect_ws(client, self.c) as ws_c:
            receive_until(ws_a, "hello")
            receive_until(ws_c, "hello")
            ws_c.send_json({"type": "lan_offer", "to": self.a, "transfer_id": 1, "payload": {}})
            self.assertEqual(receive_until(ws_c, "error")["detail"], "无法与该用户建立直传。")
            # Nothing was relayed to a: its next frame is the reply to its own ping.
            ws_a.send_json({"type": "ping"})
            self.assertEqual(ws_a.receive_json()["type"], "pong")

    def test_internal_push_endpoint_requires_token(self):
        client = self.client_as(self.a)
        with self.connect_ws(client, self.b) as ws_b:
            receive_until(ws_b, "hello")
            body = {"identities": [self.b], "event": {"type": "nudge", "conversation_key": "k", "from": self.a}}
            self.assertEqual(client.post("/api/internal/social/push", json=body).status_code, 401)
            response = client.post("/api/internal/social/push", json=body,
                                   headers={INTERNAL_TOKEN_HEADER: wanderer_internal_token()})
            self.assertEqual(response.json(), {"status": "ok", "delivered": 1})
            self.assertEqual(receive_until(ws_b, "nudge")["from"], self.a)
        self.assertEqual(social_push_service.connection_count(self.b), 0)


if __name__ == "__main__":
    unittest.main()
