"""Unit tests for Lambda handler."""

import json
import pytest
from unittest.mock import Mock, patch
from src.handler import lambda_handler


@pytest.fixture
def mock_clients():
    """Mock all external clients."""
    with patch('src.handler.ClaudeClient') as mock_claude, \
         patch('src.handler.GoogleDocsClient') as mock_docs:
        mock_claude_instance = Mock()
        mock_docs_instance = Mock()
        mock_claude.return_value = mock_claude_instance
        mock_docs.return_value = mock_docs_instance
        yield {
            'claude': mock_claude_instance,
            'docs': mock_docs_instance,
            'claude_class': mock_claude,
            'docs_class': mock_docs,
        }


def command_event(option_name: str, option_value: str) -> dict:
    """Build a Discord APPLICATION_COMMAND event with one string option."""
    return {
        "headers": {},
        "body": json.dumps({
            "type": 2,
            "token": "interaction_token_abc",
            "data": {
                "options": [{"type": 3, "name": option_name, "value": option_value}]
            }
        })
    }


class TestHandlerPingChallenge:
    """Test Discord ping challenge handling."""

    def test_responds_to_ping(self, mock_clients):
        """Should respond to Discord ping with type 1."""
        event = {
            "body": json.dumps({"type": 1, "data": {}})
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['type'] == 1

    def test_ping_with_string_body(self, mock_clients):
        """Should handle ping when body is already a string."""
        event = {
            "body": '{"type": 1, "data": {}}'
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 200


class TestHandlerDeferredResponse:
    """Slash commands must be acknowledged within Discord's 3s window."""

    def test_defers_response_for_command(self, mock_clients):
        """A valid command should get an immediate deferred ack, not the answer."""
        event = command_event("question", "Who is Alfredo?")
        response = lambda_handler(event, None)
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['type'] == 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

    def test_missing_message_returns_400(self, mock_clients):
        """Should return 400 when no message found, without deferring."""
        event = {
            "body": json.dumps({
                "type": 2,
                "data": {}
            })
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 400
        body = json.loads(response['body'])
        assert 'error' in body


class TestAsyncWorkerProcessing:
    """Test the async self-invocation that does the real classification work."""

    def async_event(self, message="Who is Alfredo?", interaction_token="interaction_token_abc"):
        return {
            'source': 'discord_async_worker',
            'message': message,
            'interaction_token': interaction_token,
        }

    def test_question_intent(self, mock_clients):
        """Should answer lore questions and post the answer as a follow-up."""
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'question',
            'confidence': 0.95,
        }
        mock_clients['claude'].answer_question.return_value = "Here's the answer about Scuz Patrol..."
        mock_clients['docs'].read_document.return_value = "Canon doc content"

        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler(self.async_event(), None)

        assert response['statusCode'] == 200
        assert mock_patch.called
        sent_content = mock_patch.call_args.kwargs['json']['content']
        assert "Here's the answer" in sent_content

    def test_new_lore_intent(self, mock_clients):
        """Should acknowledge new lore and suggest a section in the follow-up."""
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'new_lore',
            'confidence': 0.88,
            'suggested_section': 'Band Members',
        }
        mock_clients['docs'].read_document.return_value = "Canon doc content"

        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler(self.async_event(), None)

        assert response['statusCode'] == 200
        sent_content = mock_patch.call_args.kwargs['json']['content']
        assert "Band Members" in sent_content

    def test_neither_intent(self, mock_clients):
        """Should handle off-topic messages."""
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'neither',
            'confidence': 0.92,
        }
        mock_clients['docs'].read_document.return_value = "Canon doc content"

        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler(self.async_event(), None)

        assert response['statusCode'] == 200
        sent_content = mock_patch.call_args.kwargs['json']['content']
        assert "Scuz lore" in sent_content

    def test_missing_message_or_token(self, mock_clients):
        """Should skip processing and not call Discord if data is missing."""
        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler(self.async_event(message=None), None)

        assert response['statusCode'] == 400
        assert not mock_patch.called


class TestAsyncWorkerErrorHandling:
    """Test error handling within the async worker path."""

    def test_claude_init_error(self, mock_clients):
        """Should post the initialization error as the follow-up content."""
        mock_clients['claude_class'].side_effect = ValueError("Missing API key")

        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler({
                'source': 'discord_async_worker',
                'message': 'Who?',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 200
        sent_content = mock_patch.call_args.kwargs['json']['content']
        assert "Service initialization failed" in sent_content

    def test_canon_doc_read_error(self, mock_clients):
        """Should handle Google Docs read error."""
        mock_clients['docs'].read_document.side_effect = Exception("API error")

        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler({
                'source': 'discord_async_worker',
                'message': 'Who?',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 200
        sent_content = mock_patch.call_args.kwargs['json']['content']
        assert "Failed to fetch canon" in sent_content

    def test_classification_error(self, mock_clients):
        """Should handle classification API error."""
        mock_clients['claude'].classify_intent.side_effect = Exception("API error")
        mock_clients['docs'].read_document.return_value = "Canon doc"

        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler({
                'source': 'discord_async_worker',
                'message': 'Who?',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 200
        sent_content = mock_patch.call_args.kwargs['json']['content']
        assert "Classification failed" in sent_content

    def test_answer_generation_error(self, mock_clients):
        """Should handle answer generation error."""
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'question',
            'confidence': 0.95,
        }
        mock_clients['claude'].answer_question.side_effect = Exception("API error")
        mock_clients['docs'].read_document.return_value = "Canon doc"

        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler({
                'source': 'discord_async_worker',
                'message': 'Who?',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 200
        sent_content = mock_patch.call_args.kwargs['json']['content']
        assert "Failed to generate answer" in sent_content


class TestHandlerErrorHandling:
    """Test error handling."""

    def test_invalid_event_json(self, mock_clients):
        """Should handle invalid JSON in event body gracefully."""
        event = {
            "body": "not valid json"
        }
        response = lambda_handler(event, None)
        # Invalid JSON results in unknown event type, which returns 400 (no message)
        assert response['statusCode'] == 400

    def test_missing_body(self, mock_clients):
        """Should handle missing body in event."""
        event = {}
        response = lambda_handler(event, None)
        assert response['statusCode'] == 400

    def test_returns_json_response(self, mock_clients):
        """Should always return valid JSON."""
        event = {
            "body": json.dumps({"type": 1, "data": {}})
        }
        response = lambda_handler(event, None)
        assert 'statusCode' in response
        assert 'body' in response
        # Verify body is valid JSON
        json.loads(response['body'])
