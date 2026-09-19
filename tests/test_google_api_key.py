import unittest
from unittest.mock import patch

import pytest

# langchain-google-genai is NOT installable alongside the core dependency set:
# it needs httpx>=0.28.1 while mootdx pins httpx<0.26 (issue #87). There is
# deliberately no `[google]` extra, so this file stays skipped in the default
# environment — install it explicitly if you want to exercise it:
#   pip install --no-deps "langchain-google-genai>=4.0.0"
#   pip install "google-genai>=1.53.0" "httpx>=0.28.1"
# Without the skip the import below would abort collection for the whole suite.
pytest.importorskip(
    "langchain_google_genai",
    reason=(
        "optional dependency (conflicts with mootdx's httpx pin; there is no "
        "'[google]' extra) — see marvel/llm_clients/google_client.py for the "
        "explicit install commands"
    ),
)

from marvel.llm_clients.google_client import GoogleClient  # noqa: E402


@pytest.mark.unit
class TestGoogleApiKeyStandardization(unittest.TestCase):
    """Verify GoogleClient accepts unified api_key parameter."""

    @patch("marvel.llm_clients.google_client.NormalizedChatGoogleGenerativeAI")
    def test_api_key_handling(self, mock_chat):
        test_cases = [
            ("unified api_key is mapped", {"api_key": "test-key-123"}, "test-key-123"),
            ("legacy google_api_key still works", {"google_api_key": "legacy-key-456"}, "legacy-key-456"),
            ("unified api_key takes precedence", {"api_key": "unified", "google_api_key": "legacy"}, "unified"),
        ]

        for msg, kwargs, expected_key in test_cases:
            with self.subTest(msg=msg):
                mock_chat.reset_mock()
                client = GoogleClient("gemini-2.5-flash", **kwargs)
                client.get_llm()
                call_kwargs = mock_chat.call_args[1]
                self.assertEqual(call_kwargs.get("google_api_key"), expected_key)


if __name__ == "__main__":
    unittest.main()
