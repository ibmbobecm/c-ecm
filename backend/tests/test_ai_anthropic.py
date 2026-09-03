"""Tests for ai_service.py's Anthropic (Claude) backend — mirrors
test_ai_watson.py's structure for the watsonx backend. All network calls
are mocked; no live Anthropic credentials are needed.
"""
from unittest.mock import MagicMock, patch

from app import ai_service


def _anthropic_backend():
    return patch.object(ai_service, "_BACKEND", "anthropic")


class TestAnthropicBackend:
    def test_summarize_calls_anthropic(self):
        with _anthropic_backend(), patch.object(
            ai_service, "_call_anthropic", return_value="This is a summary."
        ) as mock_claude:
            result = ai_service.summarize("Some document text about invoices.")
        mock_claude.assert_called_once()
        assert result == "This is a summary."

    def test_summarize_routes_through_llm_not_other_backends(self):
        with _anthropic_backend():
            with patch.object(ai_service, "_call_anthropic", return_value="claude summary") as claude_mock, \
                 patch.object(ai_service, "_call_openai_compatible") as oa_mock, \
                 patch.object(ai_service, "_call_watsonx") as wx_mock:
                ai_service.summarize("text")
        claude_mock.assert_called_once()
        oa_mock.assert_not_called()
        wx_mock.assert_not_called()

    def test_suggest_metadata_drops_disallowed_keys(self):
        fields = [{"key": "amount", "type": "number", "label": "Amount"}]
        injected = '{"amount": 500, "admin": true, "evil": "payload"}'
        with _anthropic_backend(), patch.object(ai_service, "_call_anthropic", return_value=injected):
            result = ai_service.suggest_metadata("Invoice total $500.", fields)
        assert result == {"amount": 500}
        assert "admin" not in result and "evil" not in result

    def test_answer_question_calls_anthropic(self):
        with _anthropic_backend(), patch.object(
            ai_service, "_call_anthropic", return_value="The answer is 42."
        ):
            result = ai_service.answer_question("some document text", "What is the answer?")
        assert result == "The answer is 42."

    def test_call_anthropic_with_usage_parses_response(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "content": [{"type": "text", "text": "Hello from Claude."}],
            "usage": {"input_tokens": 12, "output_tokens": 5},
        }
        with patch.object(ai_service, "_ANTHROPIC_API_KEY", "sk-ant-test"), \
             patch.object(ai_service, "_ANTHROPIC_MODEL", "claude-sonnet-5"), \
             patch("requests.post", return_value=mock_resp) as mock_post:
            text, tokens = ai_service._call_anthropic_with_usage("Say hello")
        assert text == "Hello from Claude."
        assert tokens == 17
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["headers"]["x-api-key"] == "sk-ant-test"
        assert call_kwargs["headers"]["anthropic-version"]
        assert call_kwargs["json"]["model"] == "claude-sonnet-5"
        assert call_kwargs["json"]["messages"] == [{"role": "user", "content": "Say hello"}]

    def test_call_anthropic_with_usage_returns_empty_on_error(self):
        with patch("requests.post", side_effect=RuntimeError("network down")):
            text, tokens = ai_service._call_anthropic_with_usage("Say hello")
        assert text == ""
        assert tokens is None

    def test_llm_with_usage_reports_real_usage_for_anthropic(self):
        with _anthropic_backend(), patch.object(
            ai_service, "_call_anthropic_with_usage", return_value=("an answer", 99)
        ):
            text, tokens, is_estimated = ai_service.llm_with_usage("a prompt")
        assert text == "an answer"
        assert tokens == 99
        assert is_estimated is False

    def test_is_enabled_true_for_anthropic(self):
        with _anthropic_backend():
            assert ai_service.is_enabled() is True
            assert ai_service.get_backend_name() == "anthropic"
