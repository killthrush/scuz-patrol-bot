"""Unit tests for Claude API integration (mocked)."""

import pytest
from unittest.mock import Mock, patch
from src.claude_client import ClaudeClient


@pytest.fixture
def mock_anthropic():
    """Mock the Anthropic client."""
    with patch("src.claude_client.anthropic.Anthropic") as mock:
        yield mock


class TestClaudeClientInit:
    """Test ClaudeClient initialization."""

    def test_initializes_with_api_key(self, monkeypatch, mock_anthropic):
        """Should initialize with API key from env or argument."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key_123")

        client = ClaudeClient()

        assert client.api_key == "test_key_123"
        mock_anthropic.assert_called_once_with(api_key="test_key_123")

    def test_raises_error_without_api_key(self, monkeypatch):
        """Should raise ValueError if API key not set."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            ClaudeClient()

    def test_accepts_api_key_argument(self, mock_anthropic):
        """Should accept API key as argument."""
        ClaudeClient(api_key="override_key")

        mock_anthropic.assert_called_once_with(api_key="override_key")


class TestClassifyIntent:
    """Test intent classification."""

    def test_classifies_question(self, monkeypatch, mock_anthropic):
        """Should classify user message as question."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        # Mock the API response
        response_text = '{"intent": "question", "confidence": 0.95, "reasoning": "asking about lore"}'
        mock_response = Mock()
        mock_response.content = [Mock(text=response_text)]
        mock_response.usage = Mock(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        mock_client = Mock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        client = ClaudeClient(api_key="test_key")
        result = client.classify_intent("What is Scuz?", "Scuz is a band...")

        assert result["intent"] == "question"
        assert result["confidence"] == 0.95

    def test_classifies_new_lore(self, monkeypatch, mock_anthropic):
        """Should classify message as new lore."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        response_text = (
            '{"intent": "new_lore", "confidence": 0.88, "suggested_section": '
            '"Band Members", "reasoning": "providing new info"}'
        )
        mock_response = Mock()
        mock_response.content = [Mock(text=response_text)]
        mock_response.usage = Mock(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        mock_client = Mock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        client = ClaudeClient(api_key="test_key")
        result = client.classify_intent("Scuz was formed in 2020", "Scuz is a band...")

        assert result["intent"] == "new_lore"
        assert result["suggested_section"] == "Band Members"

    def test_classifies_neither(self, monkeypatch, mock_anthropic):
        """Should classify off-topic messages."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        mock_response = Mock()
        mock_response.content = [Mock(text='{"intent": "neither", "confidence": 0.92, "reasoning": "off-topic"}')]
        mock_response.usage = Mock(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        mock_client = Mock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        client = ClaudeClient(api_key="test_key")
        result = client.classify_intent("Anyone want pizza?", "Scuz is a band...")

        assert result["intent"] == "neither"

    def test_handles_invalid_json_response(self, monkeypatch, mock_anthropic):
        """Should handle malformed JSON from Claude."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        mock_response = Mock()
        mock_response.content = [Mock(text="not valid json")]
        mock_response.usage = Mock(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        mock_client = Mock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        client = ClaudeClient(api_key="test_key")
        result = client.classify_intent("test", "test")

        # Should return error classification
        assert result["intent"] == "neither"
        assert "error" in result.get("reasoning", "").lower() or result.get("confidence") == 0.0


class TestAnswerQuestion:
    """Test question answering."""

    def test_answers_question(self, monkeypatch, mock_anthropic):
        """Should generate an answer based on canon doc."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        answer_text = "Scuz is a fictional band formed in 2020, as documented in the Virtual Discography section."
        mock_response = Mock()
        mock_response.content = [Mock(text=answer_text)]
        mock_response.usage = Mock(
            input_tokens=500,
            output_tokens=100,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=450,  # Cache hit!
        )

        mock_client = Mock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        client = ClaudeClient(api_key="test_key")
        answer = client.answer_question("What is Scuz?", "Scuz is a band...")

        assert "Scuz" in answer
        assert "fictional" in answer

    def test_answer_includes_citations(self, monkeypatch, mock_anthropic):
        """Answer should reference the canon doc sections."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

        answer_text = "According to the Virtual Discography, Scuz released their first album in 2021."
        mock_response = Mock()
        mock_response.content = [Mock(text=answer_text)]
        mock_response.usage = Mock(
            input_tokens=500,
            output_tokens=100,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=450,
        )

        mock_client = Mock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic.return_value = mock_client

        client = ClaudeClient(api_key="test_key")
        answer = client.answer_question("When did Scuz release their first album?", "canon content")

        assert "Discography" in answer or "album" in answer.lower()
