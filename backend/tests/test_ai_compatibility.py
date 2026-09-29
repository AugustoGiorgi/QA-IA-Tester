import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import ai


def response(content='ok', reason='stop', refusal=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason, message=SimpleNamespace(content=content, refusal=refusal))])


class AICompatibilityTests(unittest.TestCase):
    def test_gpt54_actual_sdk_payload_keeps_images_and_json(self):
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={
                'id': 'test', 'object': 'chat.completion', 'created': 0, 'model': 'gpt-5.4',
                'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{"ok":true}'}}],
            })
        messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Devuelve JSON'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,fixture'}}]}]
        with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
            with OpenAI(api_key='test-only', http_client=transport) as client:
                with patch.object(ai, 'client', client), patch.object(ai, 'MODEL', 'gpt-5.4'), patch.object(ai, 'REASONING_EFFORT', 'medium'):
                    result = ai.chat_completion(messages, max_tokens=20000, response_format={'type': 'json_object'})
        body = captured[0]
        self.assertEqual(body['reasoning_effort'], 'medium')
        self.assertEqual(body['max_completion_tokens'], 20000)
        self.assertEqual(body['messages'], messages)
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        for field in ['temperature', 'top_p', 'max_tokens']:
            self.assertNotIn(field, body)
        self.assertEqual(json.loads(result.choices[0].message.content), {'ok': True})

    def test_rejects_truncated_empty_refused_results(self):
        for candidate in [response('partial', 'length'), response(''), response(' ', 'stop'), response('no', 'content_filter'), response('no', refusal='refused'), SimpleNamespace(choices=[])]:
            with self.subTest(candidate=candidate), patch.object(ai.client.chat.completions, 'create', return_value=candidate):
                with self.assertRaises(ai.AIResponseError):
                    ai.complete([{'role': 'user', 'content': 'test'}])

    def test_explicit_legacy_model_remains_compatible(self):
        with patch.object(ai, 'MODEL', 'gpt-4.1'), patch.object(ai.client.chat.completions, 'create', return_value=response()) as create:
            self.assertEqual(ai.complete([{'role': 'user', 'content': 'test'}]), 'ok')
        body = create.call_args.kwargs
        self.assertIn('temperature', body)
        self.assertNotIn('extra_body', body)


if __name__ == '__main__':
    unittest.main()
