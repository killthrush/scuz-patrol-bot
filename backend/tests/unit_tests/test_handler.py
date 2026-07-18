"""Unit tests for Lambda handler."""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock
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


class TestHandlerMessageProcessing:
    """Test message classification and response."""

    def test_question_intent(self, mock_clients):
        """Should answer lore questions."""
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'question',
            'confidence': 0.95,
        }
        mock_clients['claude'].answer_question.return_value = "Here's the answer about Scuz Patrol..."
        mock_clients['docs'].read_document.return_value = "Canon doc content"

        event = {
            "headers": {},
            "body": json.dumps({
                "type": 3,
                "data": {
                    "options": [{"type": 3, "name": "question", "value": "Who is Alfredo?"}]
                }
            })
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['intent'] == 'answer'
        assert 'answer' in body

    def test_new_lore_intent(self, mock_clients):
        """Should acknowledge new lore and suggest section."""
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'new_lore',
            'confidence': 0.88,
            'suggested_section': 'Band Members',
        }
        mock_clients['docs'].read_document.return_value = "Canon doc content"

        event = {
            "headers": {},
            "body": json.dumps({
                "type": 3,
                "data": {
                    "options": [{"type": 3, "name": "lore", "value": "New band info"}]
                }
            })
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['intent'] == 'new_lore'
        assert body['suggested_section'] == 'Band Members'
        assert 'message' in body

    def test_neither_intent(self, mock_clients):
        """Should handle off-topic messages."""
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'neither',
            'confidence': 0.92,
        }
        mock_clients['docs'].read_document.return_value = "Canon doc content"

        event = {
            "headers": {},
            "body": json.dumps({
                "type": 3,
                "data": {
                    "options": [{"type": 3, "name": "message", "value": "hello"}]
                }
            })
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['intent'] == 'neither'


class TestHandlerErrorHandling:
    """Test error handling."""

    def test_missing_message(self, mock_clients):
        """Should return 400 when no message found."""
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

    def test_claude_init_error(self, mock_clients):
        """Should handle Claude client initialization error."""
        mock_clients['claude_class'].side_effect = ValueError("Missing API key")

        event = {
            "headers": {},
            "body": json.dumps({
                "type": 3,
                "data": {
                    "options": [{"type": 3, "name": "question", "value": "Who?"}]
                }
            })
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 500
        body = json.loads(response['body'])
        assert 'error' in body

    def test_canon_doc_read_error(self, mock_clients):
        """Should handle Google Docs read error."""
        mock_clients['docs'].read_document.side_effect = Exception("API error")

        event = {
            "headers": {},
            "body": json.dumps({
                "type": 3,
                "data": {
                    "options": [{"type": 3, "name": "question", "value": "Who?"}]
                }
            })
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 500
        body = json.loads(response['body'])
        assert body['error'] == 'Failed to fetch canon'

    def test_classification_error(self, mock_clients):
        """Should handle classification API error."""
        mock_clients['claude'].classify_intent.side_effect = Exception("API error")
        mock_clients['docs'].read_document.return_value = "Canon doc"

        event = {
            "headers": {},
            "body": json.dumps({
                "type": 3,
                "data": {
                    "options": [{"type": 3, "name": "question", "value": "Who?"}]
                }
            })
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 500
        body = json.loads(response['body'])
        assert body['error'] == 'Classification failed'

    def test_answer_generation_error(self, mock_clients):
        """Should handle answer generation error."""
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'question',
            'confidence': 0.95,
        }
        mock_clients['claude'].answer_question.side_effect = Exception("API error")
        mock_clients['docs'].read_document.return_value = "Canon doc"

        event = {
            "headers": {},
            "body": json.dumps({
                "type": 3,
                "data": {
                    "options": [{"type": 3, "name": "question", "value": "Who?"}]
                }
            })
        }
        response = lambda_handler(event, None)
        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert 'error' in body

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
