"""Tests for ai_service.py — Watson AI provider paths.

These tests verify the Watson NLU, watsonx.ai, and Watson Discovery backends
plug in correctly without requiring live IBM Cloud credentials.  All network
calls are mocked.
"""
from unittest.mock import MagicMock, patch

import pytest

from app import ai_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _watsonx_backend():
    return patch.object(ai_service, "_BACKEND", "watsonx")


def _nlu_backend():
    return patch.object(ai_service, "_BACKEND", "watson_nlu")


def _disco_backend():
    return patch.object(ai_service, "_BACKEND", "watson_disco")


def _mock_iam_token():
    """Patch _get_iam_token so no real HTTP call is needed."""
    return patch.object(ai_service, "_get_iam_token", return_value="fake-iam-token")


# ---------------------------------------------------------------------------
# watsonx.ai backend
# ---------------------------------------------------------------------------

class TestWatsonxBackend:
    def test_summarize_calls_watsonx(self):
        with _watsonx_backend(), _mock_iam_token(), patch.object(
            ai_service, "_call_watsonx", return_value="This is a summary."
        ) as mock_wx:
            result = ai_service.summarize("Some document text about invoices.")
        mock_wx.assert_called_once()
        assert result == "This is a summary."

    def test_summarize_routes_through_llm(self):
        """summarize() on watsonx backend must call _call_watsonx, not _call_openai_compatible."""
        with _watsonx_backend(), _mock_iam_token():
            with patch.object(ai_service, "_call_watsonx", return_value="wx summary") as wx_mock, \
                 patch.object(ai_service, "_call_openai_compatible") as oa_mock:
                ai_service.summarize("text")
        wx_mock.assert_called_once()
        oa_mock.assert_not_called()

    def test_suggest_metadata_uses_generative_path(self):
        fields = [{"key": "vendor", "type": "text", "label": "Vendor"}]
        injected_json = '{"vendor": "Acme Corp"}'
        with _watsonx_backend(), _mock_iam_token(), patch.object(
            ai_service, "_call_watsonx", return_value=injected_json
        ):
            result = ai_service.suggest_metadata("Invoice from Acme Corp.", fields)
        assert result == {"vendor": "Acme Corp"}

    def test_suggest_metadata_still_drops_disallowed_keys(self):
        fields = [{"key": "amount", "type": "number", "label": "Amount"}]
        injected = '{"amount": 500, "admin": true, "evil": "payload"}'
        with _watsonx_backend(), _mock_iam_token(), patch.object(
            ai_service, "_call_watsonx", return_value=injected
        ):
            result = ai_service.suggest_metadata("document text", fields)
        assert result == {"amount": 500}
        assert "admin" not in result
        assert "evil" not in result

    def test_answer_question_uses_watsonx(self):
        with _watsonx_backend(), _mock_iam_token(), patch.object(
            ai_service, "_llm", return_value="The answer is 42."
        ) as mock_llm:
            result = ai_service.answer_question("doc text", "What is the answer?")
        mock_llm.assert_called_once()
        assert result == "The answer is 42."

    def test_is_enabled_true_for_watsonx(self):
        with _watsonx_backend():
            assert ai_service.is_enabled() is True


# ---------------------------------------------------------------------------
# Watson NLU backend
# ---------------------------------------------------------------------------

class TestWatsonNLUBackend:
    def test_summarize_calls_nlu_entities(self):
        with _nlu_backend(), \
             patch.object(ai_service, "_WATSON_NLU_URL", "https://nlu.example.com"), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", "nlu-key"), \
             patch.object(ai_service, "_call_watson_nlu_entities_summary", return_value="Key topics: invoice, payment") as mock_nlu:
            result = ai_service.summarize("Invoice for $500 due on Monday.")
        mock_nlu.assert_called_once()
        assert "invoice" in result.lower() or "topics" in result.lower()

    def test_summarize_nlu_returns_empty_when_no_credentials(self):
        with _nlu_backend(), \
             patch.object(ai_service, "_WATSON_NLU_URL", ""), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", ""):
            result = ai_service.summarize("some text")
        assert result == ""

    def test_suggest_metadata_nlu_extracts_entities(self):
        """Watson NLU path for suggest_metadata uses NLU entity extraction."""
        import requests as req_module
        fields = [{"key": "person_name", "type": "text", "label": "Person Name"}]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "entities": [{"type": "Person", "text": "John Smith"}],
            "keywords": [],
        }
        with _nlu_backend(), \
             patch.object(ai_service, "_WATSON_NLU_URL", "https://nlu.example.com"), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", "nlu-key"), \
             patch("requests.post", return_value=mock_resp):
            result = ai_service.suggest_metadata("Call John Smith tomorrow.", fields)
        assert result.get("person_name") == "John Smith"

    def test_answer_question_nlu_returns_informational_message(self):
        """Watson NLU is not a generative Q&A backend — should surface a helpful message."""
        with _nlu_backend():
            result = ai_service.answer_question("some text", "What is this about?")
        assert "watson nlu" in result.lower() or "classification" in result.lower()

    def test_suggest_workflow_uses_nlu_when_configured(self):
        workflow_names = ["Finance Approval", "Legal Review", "Compliance Review"]
        with patch.object(ai_service, "_WATSON_NLU_URL", "https://nlu.example.com"), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", "nlu-key"), \
             patch.object(ai_service, "_call_watson_nlu_classify", return_value="Finance Approval"):
            result = ai_service.suggest_workflow("Invoice for $5000.", None, workflow_names)
        assert result == "Finance Approval"


# ---------------------------------------------------------------------------
# Watson Discovery backend
# ---------------------------------------------------------------------------

class TestWatsonDiscoveryBackend:
    def test_answer_question_uses_discovery_passages(self):
        with _disco_backend(), \
             patch.object(ai_service, "_WATSON_DISCO_URL", "https://disco.example.com"), \
             patch.object(ai_service, "_WATSON_DISCO_APIKEY", "disco-key"), \
             patch.object(ai_service, "_WATSON_DISCO_PROJECT_ID", "proj-123"), \
             patch.object(ai_service, "_call_watson_discovery_ask", return_value="The contract starts January 2024.") as mock_disco, \
             patch.object(ai_service, "_IBM_CLOUD_API_KEY", ""), \
             patch.object(ai_service, "_WATSONX_PROJECT_ID", ""):
            result = ai_service.answer_question("", "When does the contract start?")
        mock_disco.assert_called_once_with("When does the contract start?")
        assert "January 2024" in result

    def test_answer_question_discovery_with_watsonx_generative_answer(self):
        with _disco_backend(), \
             patch.object(ai_service, "_WATSON_DISCO_URL", "https://disco.example.com"), \
             patch.object(ai_service, "_WATSON_DISCO_APIKEY", "disco-key"), \
             patch.object(ai_service, "_WATSON_DISCO_PROJECT_ID", "proj-123"), \
             patch.object(ai_service, "_call_watson_discovery_ask", return_value="passage about contracts"), \
             patch.object(ai_service, "_IBM_CLOUD_API_KEY", "ibm-key"), \
             patch.object(ai_service, "_WATSONX_PROJECT_ID", "proj-watsonx"), \
             patch.object(ai_service, "_call_watsonx", return_value="The contract expires in 2025.") as mock_wx:
            result = ai_service.answer_question("", "When does the contract expire?")
        mock_wx.assert_called_once()
        assert result == "The contract expires in 2025."

    def test_answer_question_discovery_no_passages_returns_informational(self):
        with _disco_backend(), \
             patch.object(ai_service, "_call_watson_discovery_ask", return_value=""):
            result = ai_service.answer_question("", "Any question?")
        assert "no relevant" in result.lower() or "not found" in result.lower() or "passages" in result.lower()

    def test_summarize_disco_with_watsonx_fallback(self):
        with _disco_backend(), \
             patch.object(ai_service, "_IBM_CLOUD_API_KEY", "ibm-key"), \
             patch.object(ai_service, "_WATSONX_PROJECT_ID", "proj-id"), \
             patch.object(ai_service, "_call_watsonx", return_value="Generated summary.") as mock_wx:
            result = ai_service.summarize("text")
        mock_wx.assert_called_once()
        assert result == "Generated summary."


# ---------------------------------------------------------------------------
# suggest_workflow — keyword fallback (no NLU configured)
# ---------------------------------------------------------------------------

class TestSuggestWorkflow:
    def test_invoice_text_suggests_finance_workflow(self):
        names = ["Finance Approval", "Legal Review"]
        with patch.object(ai_service, "_WATSON_NLU_URL", ""), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", ""):
            result = ai_service.suggest_workflow("Invoice #1234 total $5000 payment due.", None, names)
        assert result == "Finance Approval"

    def test_contract_text_suggests_legal_workflow(self):
        names = ["Finance Approval", "Legal Review", "Compliance Review"]
        with patch.object(ai_service, "_WATSON_NLU_URL", ""), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", ""):
            result = ai_service.suggest_workflow("This agreement is entered into between...", None, names)
        assert result == "Legal Review"

    def test_no_match_returns_none(self):
        names = ["Finance Approval", "Legal Review"]
        with patch.object(ai_service, "_WATSON_NLU_URL", ""), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", ""):
            result = ai_service.suggest_workflow("A photo of a cat.", None, names)
        assert result is None

    def test_empty_workflow_list_returns_none(self):
        result = ai_service.suggest_workflow("Invoice text", None, [])
        assert result is None

    def test_class_label_used_as_fallback(self):
        names = ["Compliance Review", "Finance Approval"]
        with patch.object(ai_service, "_WATSON_NLU_URL", ""), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", ""):
            # Document text is generic, but class label says "policy"
            result = ai_service.suggest_workflow("Some document.", "Policy Document", names)
        assert result == "Compliance Review"


class TestSuggestWorkflowWithSource:
    """suggest_workflow() itself only ever returned the name, so a caller
    (the /suggest_workflow router) had no way to tell whether NLU actually
    produced the match or the keyword fallback did -- it approximated this
    by checking whether NLU was merely *configured*, which is wrong
    whenever NLU is configured but inconclusive for a given document.
    suggest_workflow_with_source() reports the real source.
    """

    def test_source_is_nlu_when_nlu_produces_the_match(self):
        names = ["Finance Approval", "Legal Review"]
        with patch.object(ai_service, "_WATSON_NLU_URL", "https://nlu.example.com"), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", "nlu-key"), \
             patch.object(ai_service, "_call_watson_nlu_classify", return_value="Finance Approval"):
            result = ai_service.suggest_workflow_with_source("Invoice for $5000.", None, names)
        assert result.name == "Finance Approval"
        assert result.source == "nlu"

    def test_source_is_keyword_when_nlu_configured_but_inconclusive(self):
        """The exact bug this exists to catch: NLU is configured (so the
        old confidence check would have said "high") but returns no
        confident label for this document, so the keyword map is what
        actually finds it -- source must say "keyword", not "nlu"."""
        names = ["Finance Approval", "Legal Review"]
        with patch.object(ai_service, "_WATSON_NLU_URL", "https://nlu.example.com"), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", "nlu-key"), \
             patch.object(ai_service, "_call_watson_nlu_classify", return_value=None):
            result = ai_service.suggest_workflow_with_source(
                "Invoice #1234 total $5000 payment due.", None, names
            )
        assert result.name == "Finance Approval"
        assert result.source == "keyword"

    def test_source_is_keyword_when_nlu_not_configured(self):
        names = ["Finance Approval", "Legal Review"]
        with patch.object(ai_service, "_WATSON_NLU_URL", ""), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", ""):
            result = ai_service.suggest_workflow_with_source(
                "Invoice #1234 total $5000 payment due.", None, names
            )
        assert result.source == "keyword"

    def test_source_is_none_when_nothing_matches(self):
        names = ["Finance Approval", "Legal Review"]
        with patch.object(ai_service, "_WATSON_NLU_URL", ""), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", ""):
            result = ai_service.suggest_workflow_with_source("A photo of a cat.", None, names)
        assert result.name is None
        assert result.source == "none"

    def test_plain_suggest_workflow_still_returns_just_the_name(self):
        """Backward-compat wrapper — existing callers/tests keep working."""
        names = ["Finance Approval"]
        with patch.object(ai_service, "_WATSON_NLU_URL", ""), \
             patch.object(ai_service, "_WATSON_NLU_APIKEY", ""):
            result = ai_service.suggest_workflow("Invoice #1234 total $5000 payment due.", None, names)
        assert result == "Finance Approval"
