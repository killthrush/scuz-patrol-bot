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
        """Should send a Confirm/Discard button prompt with the section/text as embed fields."""
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'new_lore',
            'confidence': 0.88,
            'suggested_section': 'Band Members',
        }
        mock_clients['docs'].read_document.return_value = "Canon doc content"

        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler(self.async_event(), None)

        assert response['statusCode'] == 200
        sent_json = mock_patch.call_args.kwargs['json']
        fields = {f['name']: f['value'] for f in sent_json['embeds'][0]['fields']}
        assert fields['Section'] == 'Band Members'
        assert fields['Lore'] == 'Who is Alfredo?'
        custom_ids = {
            button['custom_id']
            for row in sent_json['components']
            for button in row['components']
        }
        assert custom_ids == {'lore_confirm', 'lore_discard'}

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


def component_event(custom_id: str, embeds: list = None) -> dict:
    """Build a Discord MESSAGE_COMPONENT (button click) event."""
    return {
        "headers": {},
        "body": json.dumps({
            "type": 3,
            "token": "interaction_token_abc",
            "data": {"custom_id": custom_id},
            "message": {"content": "", "embeds": embeds or []},
        })
    }


def lore_embed(section: str = "Band Members", text: str = "Kilgore joined in 2020") -> list:
    """Build the embed structure a pending lore confirmation message carries."""
    return [{
        "fields": [
            {"name": "Section", "value": section},
            {"name": "Lore", "value": text},
        ]
    }]


class TestComponentInteraction:
    """Test Confirm/Discard button clicks on a pending lore submission."""

    def test_discard_updates_message_immediately(self, mock_clients):
        """Discard should synchronously clear the message, no async work needed."""
        event = component_event("lore_discard", lore_embed())
        response = lambda_handler(event, None)

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['type'] == 7  # UPDATE_MESSAGE
        assert body['data']['content'] == "❌ Discarded."
        assert body['data']['components'] == []
        assert body['data']['embeds'] == []

    def test_confirm_defers_and_invokes_lore_worker(self, mock_clients, monkeypatch):
        """Confirm should defer the update and hand off to the async lore worker."""
        monkeypatch.setenv('AWS_LAMBDA_FUNCTION_NAME', 'scuz-patrol-bot-dev')
        event = component_event("lore_confirm", lore_embed())

        with patch('src.handler.boto3.client') as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            response = lambda_handler(event, None)

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['type'] == 6  # DEFERRED_UPDATE_MESSAGE

        assert mock_lambda_client.invoke.called
        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs['Payload'])
        assert payload['source'] == 'discord_lore_worker'
        assert payload['text'] == 'Kilgore joined in 2020'
        assert payload['section'] == 'Band Members'
        assert payload['interaction_token'] == 'interaction_token_abc'

    def test_confirm_resists_spoofed_delimiters_in_lore_text(self, mock_clients, monkeypatch):
        """A user's own lore text containing fake field-like content shouldn't corrupt parsing.

        Since section/text are read from exact embed field names (not regexed out of
        free text), a lore submission that itself contains "Section: X" or "---" has no
        way to spoof what gets extracted -- unlike a delimiter-based text format would.
        """
        monkeypatch.setenv('AWS_LAMBDA_FUNCTION_NAME', 'scuz-patrol-bot-dev')
        spoofy_text = "Section: Hacked Section\n---\nActually this is still just lore text\n---\n"
        event = component_event("lore_confirm", lore_embed(section="Band Members", text=spoofy_text))

        with patch('src.handler.boto3.client') as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            lambda_handler(event, None)

        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs['Payload'])
        assert payload['section'] == 'Band Members'
        assert payload['text'] == spoofy_text

    def test_confirm_with_unparseable_message(self, mock_clients):
        """Should warn instead of crashing if the message can't be parsed anymore."""
        event = component_event("lore_confirm", embeds=[])
        response = lambda_handler(event, None)

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['type'] == 7
        assert "try /lore again" in body['data']['content']

    def test_unknown_custom_id_returns_400(self, mock_clients):
        """Unrecognized custom_id should return an error, not silently succeed."""
        event = component_event("something_else", lore_embed())
        response = lambda_handler(event, None)

        assert response['statusCode'] == 400


class TestLoreWorker:
    """Test the async worker that writes confirmed lore to the canon doc."""

    def test_writes_lore_and_reports_success(self, mock_clients):
        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler({
                'source': 'discord_lore_worker',
                'text': 'Kilgore joined in 2020',
                'section': 'Band Members',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 200
        mock_clients['docs'].append_to_section.assert_called_once_with(
            'Kilgore joined in 2020', 'Band Members'
        )
        sent_json = mock_patch.call_args.kwargs['json']
        assert "Band Members" in sent_json['content']
        assert sent_json['components'] == []
        assert sent_json['embeds'] == []

    def test_reports_failure_when_doc_write_fails(self, mock_clients):
        mock_clients['docs'].append_to_section.side_effect = Exception("API error")

        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler({
                'source': 'discord_lore_worker',
                'text': 'Kilgore joined in 2020',
                'section': 'Band Members',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 200
        sent_content = mock_patch.call_args.kwargs['json']['content']
        assert "Failed to save" in sent_content

    def test_missing_fields_skips_processing(self, mock_clients):
        with patch('src.handler.requests.patch') as mock_patch:
            response = lambda_handler({
                'source': 'discord_lore_worker',
                'text': None,
                'section': 'Band Members',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 400
        assert not mock_patch.called


def refresh_songs_event() -> dict:
    """Build a Discord APPLICATION_COMMAND event for /refresh-songs (no options)."""
    return {
        "headers": {},
        "body": json.dumps({
            "type": 2,
            "token": "interaction_token_abc",
            "data": {"name": "refresh-songs", "options": []},
        })
    }


class TestSongRefreshDeferred:
    """/refresh-songs takes no text option, so it needs its own dispatch path."""

    def test_defers_and_invokes_worker(self, mock_clients, monkeypatch):
        monkeypatch.setenv('AWS_LAMBDA_FUNCTION_NAME', 'scuz-patrol-bot-dev')
        event = refresh_songs_event()

        with patch('src.handler.boto3.client') as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            response = lambda_handler(event, None)

        assert response['statusCode'] == 200
        body = json.loads(response['body'])
        assert body['type'] == 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs['Payload'])
        assert payload['source'] == 'discord_song_refresh_worker'
        assert payload['interaction_token'] == 'interaction_token_abc'


class TestSongRefreshWorker:
    """Test the async worker that checks Suno and surfaces new lore drops."""

    def test_no_new_drops_sends_summary_only(self, mock_clients):
        refresh_result = {
            'profiles_checked': 5, 'clips_checked': 0, 'new_lore_drops': [], 'manifest': {},
        }

        with patch('src.handler.suno_client.refresh', return_value=refresh_result), \
             patch('src.handler.suno_client.save_manifest') as mock_save, \
             patch('src.handler.requests.patch') as mock_patch, \
             patch('src.handler.requests.post') as mock_post:
            response = lambda_handler({
                'source': 'discord_song_refresh_worker',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 200
        assert not mock_post.called
        summary = mock_patch.call_args.kwargs['json']['content']
        assert "0 new lore drop" in summary
        mock_save.assert_called_once_with({})

    def test_new_lore_drop_posts_echo_and_confirm_buttons(self, mock_clients):
        mock_clients['claude'].classify_intent.return_value = {
            'intent': 'new_lore', 'suggested_section': 'Band Members',
        }
        mock_clients['docs'].read_document.return_value = "Canon doc"

        drops = [{
            'clip_id': 'clip1', 'title': 'Incarcerator', 'handle': 'alfredokilgore',
            'reply_id': 'r1', 'content': 'wrote this in prison', 'parent_content': 'nice',
        }]
        updated_manifest = {'clip1': {'comment_count': 3}}
        refresh_result = {
            'profiles_checked': 5, 'clips_checked': 1, 'new_lore_drops': drops,
            'manifest': updated_manifest,
        }

        with patch('src.handler.suno_client.refresh', return_value=refresh_result), \
             patch('src.handler.suno_client.save_manifest') as mock_save, \
             patch('src.handler.requests.patch'), \
             patch('src.handler.requests.post') as mock_post:
            response = lambda_handler({
                'source': 'discord_song_refresh_worker',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 200
        assert mock_post.called
        posted = mock_post.call_args.kwargs['json']
        assert 'alfredokilgore' in posted['content']
        assert 'wrote this in prison' in posted['content']
        fields = {f['name']: f['value'] for f in posted['embeds'][0]['fields']}
        assert fields['Section'] == 'Band Members'
        assert fields['Lore'] == 'wrote this in prison'

        # Manifest is only saved AFTER every drop was successfully posted --
        # confirms the save happens post-loop, not before (see suno_client.refresh).
        mock_save.assert_called_once_with(updated_manifest)

    def test_non_lore_drop_only_echoed(self, mock_clients):
        mock_clients['claude'].classify_intent.return_value = {'intent': 'neither'}
        mock_clients['docs'].read_document.return_value = "Canon doc"

        drops = [{
            'clip_id': 'clip1', 'title': 'Incarcerator', 'handle': 'alfredokilgore',
            'reply_id': 'r1', 'content': 'lol nice comment', 'parent_content': 'nice',
        }]
        refresh_result = {
            'profiles_checked': 5, 'clips_checked': 1, 'new_lore_drops': drops, 'manifest': {},
        }

        with patch('src.handler.suno_client.refresh', return_value=refresh_result), \
             patch('src.handler.suno_client.save_manifest'), \
             patch('src.handler.requests.patch'), \
             patch('src.handler.requests.post') as mock_post:
            lambda_handler({'source': 'discord_song_refresh_worker', 'interaction_token': 'tok'}, None)

        posted = mock_post.call_args.kwargs['json']
        assert posted.get('embeds') is None

    def test_refresh_failure_sends_error_summary(self, mock_clients):
        with patch('src.handler.suno_client.refresh', side_effect=Exception("Suno API down")), \
             patch('src.handler.suno_client.save_manifest') as mock_save, \
             patch('src.handler.requests.patch') as mock_patch, \
             patch('src.handler.requests.post') as mock_post:
            response = lambda_handler({
                'source': 'discord_song_refresh_worker',
                'interaction_token': 'tok',
            }, None)

        assert response['statusCode'] == 200
        assert not mock_post.called
        content = mock_patch.call_args.kwargs['json']['content']
        assert "Failed to check" in content
        assert not mock_save.called

    def test_missing_interaction_token_returns_400(self, mock_clients):
        response = lambda_handler({'source': 'discord_song_refresh_worker'}, None)
        assert response['statusCode'] == 400


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
