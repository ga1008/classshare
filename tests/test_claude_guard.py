import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.claude_guard import (
    DpapiProtector,
    EventStore,
    ExecutableInspector,
    ProxyController,
    advisory_status,
    ai_safe_domain,
    analyze_snapshot,
    build_ai_payload,
    confidence_score,
    parse_ai_json,
    protocol_assessment,
    redact_text,
    select_ai_provider,
    startup_command,
)


class StubInspector(ExecutableInspector):
    def inspect(self, raw_path: str):
        if "claude-code" in raw_path.lower():
            version = "2.1.205.0"
            path_class = "claude_code_managed"
        else:
            version = "1.20186.1"
            path_class = "microsoft_store"
        return {
            "path": "%APPDATA%\\Claude\\claude.exe",
            "path_class": path_class,
            "signature_status": "Valid",
            "vendor_signed": True,
            "signer": "Anthropic, PBC",
            "sha256": "A" * 64,
            "version": version,
            "product": "Claude",
            "length": 123,
            "last_write_utc": "2026-07-13T00:00:00Z",
        }


def fixture_snapshot():
    return {
        "CapturedAt": "2026-07-13T08:00:00+00:00",
        "Processes": [
            {
                "ProcessId": 100,
                "ParentProcessId": 1,
                "Name": "claude.exe",
                "ExecutablePath": r"C:\Users\Tester\AppData\Roaming\Claude\claude-code\2.1.205\claude.exe",
                "CommandLine": "claude",
            },
            {
                "ProcessId": 200,
                "ParentProcessId": 1,
                "Name": "clashmiService.exe",
                "ExecutablePath": "",
                "CommandLine": "",
            },
        ],
        "Tcp": [
            {
                "OwningProcess": 200,
                "State": 2,
                "LocalAddress": "::",
                "LocalPort": 7890,
                "RemoteAddress": "::",
                "RemotePort": 0,
            },
            {
                "OwningProcess": 100,
                "State": 5,
                "LocalAddress": "127.0.0.1",
                "LocalPort": 50123,
                "RemoteAddress": "127.0.0.1",
                "RemotePort": 7890,
            },
            {
                "OwningProcess": 200,
                "State": 5,
                "LocalAddress": "10.0.0.2",
                "LocalPort": 51000,
                "RemoteAddress": "203.0.113.20",
                "RemotePort": 443,
            },
        ],
        "Dns": [{"Entry": "api.anthropic.com", "Data": "203.0.113.20", "Type": 1}],
    }


class ClaudeGuardTests(unittest.TestCase):
    def test_advisory_range_only_applies_to_claude_code(self):
        self.assertEqual("affected", advisory_status("2.1.91", "claude_code"))
        self.assertEqual("affected", advisory_status("2.1.196.0", "claude_code"))
        self.assertEqual("newer_than_affected_range", advisory_status("2.1.205", "claude_code"))
        self.assertEqual("not_applicable", advisory_status("1.20186.1", "claude_desktop"))

    def test_tls_port_is_marked_as_expected_not_proven(self):
        result = protocol_assessment("8.8.8.8", 443)
        self.assertEqual("encrypted_expected", result["security"])
        self.assertEqual("port_convention_only", result["confidence"])

    def test_redactor_removes_common_secret_shapes(self):
        text = redact_text("api_key=top-secret-value password=hunter2 sk-abcdefghijklmnop")
        self.assertNotIn("top-secret-value", text)
        self.assertNotIn("hunter2", text)
        self.assertNotIn("abcdefghijklmnop", text)

    def test_proxy_without_controller_is_never_precisely_attributed(self):
        status, events = analyze_snapshot(fixture_snapshot(), StubInspector(), controller_status="authentication_required")
        self.assertTrue(status["proxy"]["detected"])
        self.assertEqual("shared_candidate_only", status["proxy"]["attribution"])
        candidate = [item for item in status["connections"] if item["attribution"] == "shared_proxy_candidate"]
        self.assertEqual(1, len(candidate))
        candidate_event = next(item for item in events if item["details"].get("attribution") == "shared_proxy_candidate")
        self.assertLessEqual(candidate_event["severity"], 1)

    def test_proxy_controller_source_port_enables_exact_attribution(self):
        controller = [
            {
                "metadata": {
                    "sourcePort": "50123",
                    "destinationIP": "203.0.113.20",
                    "destinationPort": "443",
                    "host": "api.anthropic.com",
                    "network": "tcp",
                    "process": "claude.exe",
                }
            }
        ]
        status, _events = analyze_snapshot(
            fixture_snapshot(), StubInspector(), controller_connections=controller, controller_status="available"
        )
        self.assertEqual("exact", status["proxy"]["attribution"])
        exact = [item for item in status["connections"] if item["attribution"] == "proxy_exact"]
        self.assertEqual("api.anthropic.com", exact[0]["domains"][0])

    def test_ai_payload_omits_paths_ips_and_private_domains(self):
        status, events = analyze_snapshot(fixture_snapshot(), StubInspector(), controller_status="authentication_required")
        payload = build_ai_payload(status, events)
        serialized = json.dumps(payload)
        self.assertNotIn("C:\\\\Users", serialized)
        self.assertNotIn("203.0.113.20", serialized)
        self.assertEqual("private-domain-" + __import__("hashlib").sha256(b"research.internal").hexdigest()[:12], ai_safe_domain("research.internal"))

    def test_ai_provider_uses_fixed_endpoint_definition(self):
        provider = select_ai_provider({"DEEPSEEK_API_KEY": "secret", "DEEPSEEK_MODEL_STANDARD": "deepseek-chat"})
        self.assertEqual("deepseek", provider["name"])
        self.assertEqual("https://api.deepseek.com/chat/completions", provider["url"])

    def test_ai_response_is_schema_limited_and_redacted(self):
        report = parse_ai_json(
            json.dumps(
                {
                    "risk_level": "high",
                    "confidence": 84,
                    "summary": "api_key=do-not-store-this",
                    "findings": [{"code": "X", "severity": "high", "summary": "test", "evidence": "metadata"}],
                    "recommended_actions": ["review"],
                    "limitations": "TLS content unavailable",
                    "unexpected": "ignored",
                }
            )
        )
        self.assertNotIn("do-not-store-this", report["summary"])
        self.assertNotIn("unexpected", report)

    def test_ai_response_extracts_json_object_from_wrapping_text(self):
        report = parse_ai_json(
            'Analysis follows:\n{"risk_level":"low","confidence":0.61,"summary":"ok",'
            '"findings":[],"recommended_actions":[],"limitations":"metadata only"}\nDone.'
        )
        self.assertEqual("low", report["risk_level"])
        self.assertEqual(61, report["confidence"])

    def test_confidence_accepts_fraction_integer_and_percent(self):
        self.assertEqual(72, confidence_score(0.72))
        self.assertEqual(72, confidence_score(72))
        self.assertEqual(72, confidence_score("72%"))

    def test_dpapi_database_does_not_contain_event_details_in_plaintext(self):
        if os.name != "nt":
            self.skipTest("Windows DPAPI test")
        secret_marker = "highly-sensitive-project-name-7c6710"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protector = DpapiProtector()
            store = EventStore(root, protector)
            store.upsert_event(
                {
                    "fingerprint": "f" * 64,
                    "kind": "test",
                    "severity": 2,
                    "title": "test",
                    "details": {"path": secret_marker},
                },
                "2026-07-13T08:00:00+00:00",
            )
            self.assertEqual(secret_marker, store.events()[0]["details"]["path"])
            store.close()
            raw = b"".join(path.read_bytes() for path in root.iterdir() if path.is_file())
        self.assertNotIn(secret_marker.encode(), raw)

    def test_proxy_controller_rejects_non_loopback_endpoint(self):
        with self.assertRaises(ValueError):
            ProxyController("http://example.com:9090")

    def test_startup_command_never_embeds_ai_key(self):
        command = startup_command()
        self.assertIn("pythonw.exe", command.lower())
        self.assertNotIn("API_KEY", command)
        self.assertNotIn("secret", command.lower())


if __name__ == "__main__":
    unittest.main()
