import unittest

from ai_assistant import _extract_message_tool_calls


class _FunctionCall:
    name = "platform_query"
    arguments = '{"view":"class_roster","params":{"class_keyword":"三班"}}'


class _ToolCall:
    id = "call-1"
    type = "function"
    function = _FunctionCall()


class _Message:
    tool_calls = [_ToolCall()]


class AiAssistantToolCallTests(unittest.TestCase):
    def test_extract_message_tool_calls_from_sdk_objects(self):
        calls = _extract_message_tool_calls(_Message())

        self.assertEqual(
            [
                {
                    "id": "call-1",
                    "type": "function",
                    "name": "platform_query",
                    "arguments": {"view": "class_roster", "params": {"class_keyword": "三班"}},
                }
            ],
            calls,
        )

    def test_extract_message_tool_calls_from_dict_payload(self):
        calls = _extract_message_tool_calls(
            {
                "tool_calls": [
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {
                            "name": "platform_query",
                            "arguments": '{"view":"my_schedule","params":{}}',
                        },
                    }
                ]
            }
        )

        self.assertEqual("platform_query", calls[0]["name"])
        self.assertEqual({"view": "my_schedule", "params": {}}, calls[0]["arguments"])


if __name__ == "__main__":
    unittest.main()
