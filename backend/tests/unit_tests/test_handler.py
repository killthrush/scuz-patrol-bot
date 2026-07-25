"""Unit tests for Lambda handler."""

import json
import pytest
from unittest.mock import Mock, patch
from src.handler import lambda_handler, reconstruct_handler


@pytest.fixture
def mock_clients():
    """Mock all external clients."""
    with patch("src.handler.ClaudeClient") as mock_claude, patch(
        "src.handler.GoogleDocsClient"
    ) as mock_docs:
        mock_claude_instance = Mock()
        mock_docs_instance = Mock()
        mock_claude.return_value = mock_claude_instance
        mock_docs.return_value = mock_docs_instance
        yield {
            "claude": mock_claude_instance,
            "docs": mock_docs_instance,
            "claude_class": mock_claude,
            "docs_class": mock_docs,
        }


def command_event(
    option_name: str, option_value: str, user_name: str = "killthrush"
) -> dict:
    """Build a Discord APPLICATION_COMMAND event with one string option."""
    return {
        "headers": {},
        "body": json.dumps(
            {
                "type": 2,
                "token": "interaction_token_abc",
                "member": {"user": {"id": "123", "username": user_name}},
                "data": {
                    "options": [{"type": 3, "name": option_name, "value": option_value}]
                },
            }
        ),
    }


class TestHandlerPingChallenge:
    """Test Discord ping challenge handling."""

    def test_responds_to_ping(self, mock_clients):
        """Should respond to Discord ping with type 1."""
        event = {"body": json.dumps({"type": 1, "data": {}})}
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 1

    def test_ping_with_string_body(self, mock_clients):
        """Should handle ping when body is already a string."""
        event = {"body": '{"type": 1, "data": {}}'}
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200


class TestHandlerDeferredResponse:
    """Slash commands must be acknowledged within Discord's 3s window."""

    def test_defers_response_for_command(self, mock_clients):
        """A valid command should get an immediate deferred ack, not the answer."""
        event = command_event("question", "Who is Alfredo?")
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

    def test_defer_threads_submitter_username_to_async_worker(
        self, mock_clients, monkeypatch
    ):
        """The command invoker's username should flow through to the async worker payload.

        This is what lets a later lore confirmation record who actually wrote the
        lore, since the async worker has no other way to know who invoked /lore.
        """
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        event = command_event(
            "question", "Kilgore joined in 2020", user_name="metrivus"
        )

        with patch("src.handler.boto3.client") as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            lambda_handler(event, None)

        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["user_name"] == "metrivus"

    def test_missing_message_returns_400(self, mock_clients):
        """Should return 400 when no message found, without deferring."""
        event = {"body": json.dumps({"type": 2, "data": {}})}
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "error" in body


class TestAsyncWorkerProcessing:
    """Test the async self-invocation that does the real classification work."""

    def async_event(
        self,
        message="Who is Alfredo?",
        interaction_token="interaction_token_abc",
        user_name="killthrush",
    ):
        return {
            "source": "discord_async_worker",
            "message": message,
            "interaction_token": interaction_token,
            "user_name": user_name,
        }

    def test_question_intent(self, mock_clients):
        """Should answer lore questions and post the answer as a follow-up."""
        mock_clients["claude"].classify_intent.return_value = {
            "intent": "question",
            "confidence": 0.95,
        }
        mock_clients[
            "claude"
        ].answer_question.return_value = "Here's the answer about Scuz Patrol..."
        mock_clients["docs"].read_document.return_value = "Canon doc content"

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(self.async_event(), None)

        assert response["statusCode"] == 200
        assert mock_patch.called
        sent_content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Here's the answer" in sent_content

    def test_new_lore_intent(self, mock_clients):
        """Should send a Confirm/Discard prompt with lore text as content, section/submitter as embed fields."""
        mock_clients["claude"].classify_intent.return_value = {
            "intent": "new_lore",
            "confidence": 0.88,
            "suggested_section": "Band Members",
        }
        mock_clients["docs"].read_document.return_value = "Canon doc content"

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(self.async_event(), None)

        assert response["statusCode"] == 200
        sent_json = mock_patch.call_args.kwargs["json"]
        assert sent_json["content"] == "Who is Alfredo?"
        fields = {f["name"]: f["value"] for f in sent_json["embeds"][0]["fields"]}
        assert fields["Section"] == "Band Members"
        assert fields["Submitted by"] == "killthrush"
        custom_ids = {
            button["custom_id"]
            for row in sent_json["components"]
            for button in row["components"]
        }
        assert custom_ids == {"lore_confirm", "lore_discard"}

    def test_new_lore_intent_over_length_cap_is_rejected(self, mock_clients):
        """A lore submission over MAX_FACT_LENGTH should get an error, not a confirm prompt."""
        from src.fact_store import MAX_FACT_LENGTH

        mock_clients["claude"].classify_intent.return_value = {
            "intent": "new_lore",
            "suggested_section": "Band Members",
        }
        mock_clients["docs"].read_document.return_value = "Canon doc content"
        too_long = "x" * (MAX_FACT_LENGTH + 1)

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(self.async_event(message=too_long), None)

        assert response["statusCode"] == 200
        sent_json = mock_patch.call_args.kwargs["json"]
        assert str(MAX_FACT_LENGTH) in sent_json["content"]
        assert sent_json.get("components") is None
        assert sent_json.get("embeds") is None

    def test_neither_intent(self, mock_clients):
        """Should handle off-topic messages."""
        mock_clients["claude"].classify_intent.return_value = {
            "intent": "neither",
            "confidence": 0.92,
        }
        mock_clients["docs"].read_document.return_value = "Canon doc content"

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(self.async_event(), None)

        assert response["statusCode"] == 200
        sent_content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Scuz lore" in sent_content

    def test_missing_message_or_token(self, mock_clients):
        """Should skip processing and not call Discord if data is missing."""
        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(self.async_event(message=None), None)

        assert response["statusCode"] == 400
        assert not mock_patch.called


class TestAsyncWorkerErrorHandling:
    """Test error handling within the async worker path."""

    def test_claude_init_error(self, mock_clients):
        """Should post the initialization error as the follow-up content."""
        mock_clients["claude_class"].side_effect = ValueError("Missing API key")

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_async_worker",
                    "message": "Who?",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        sent_content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Service initialization failed" in sent_content

    def test_canon_doc_read_error(self, mock_clients):
        """Should handle Google Docs read error."""
        mock_clients["docs"].read_document.side_effect = Exception("API error")

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_async_worker",
                    "message": "Who?",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        sent_content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Failed to fetch canon" in sent_content

    def test_classification_error(self, mock_clients):
        """Should handle classification API error."""
        mock_clients["claude"].classify_intent.side_effect = Exception("API error")
        mock_clients["docs"].read_document.return_value = "Canon doc"

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_async_worker",
                    "message": "Who?",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        sent_content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Classification failed" in sent_content

    def test_answer_generation_error(self, mock_clients):
        """Should handle answer generation error."""
        mock_clients["claude"].classify_intent.return_value = {
            "intent": "question",
            "confidence": 0.95,
        }
        mock_clients["claude"].answer_question.side_effect = Exception("API error")
        mock_clients["docs"].read_document.return_value = "Canon doc"

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_async_worker",
                    "message": "Who?",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        sent_content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Failed to generate answer" in sent_content


def component_event(custom_id: str, content: str = "", embeds: list = None) -> dict:
    """Build a Discord MESSAGE_COMPONENT (button click) event."""
    return {
        "headers": {},
        "body": json.dumps(
            {
                "type": 3,
                "token": "interaction_token_abc",
                "data": {"custom_id": custom_id},
                "message": {"content": content, "embeds": embeds or []},
            }
        ),
    }


def lore_embed(section: str = "Band Members", submitted_by: str = "killthrush") -> list:
    """Build the embed structure a pending lore confirmation message carries.

    The lore text itself isn't here -- it lives in the message content (see
    component_event), since it can run past Discord's 1024-char embed field cap.
    """
    return [
        {
            "fields": [
                {"name": "Section", "value": section},
                {"name": "Submitted by", "value": submitted_by},
            ]
        }
    ]


class TestComponentInteraction:
    """Test Confirm/Discard button clicks on a pending lore submission."""

    def test_discard_updates_message_immediately(self, mock_clients):
        """Discard should synchronously update the message, no async work needed.

        The original lore text stays visible (in content) so people scrolling
        back can see what was discarded, instead of a bare status line.
        """
        event = component_event("lore_discard", "Kilgore joined in 2020", lore_embed())
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 7  # UPDATE_MESSAGE
        assert body["data"]["components"] == []
        assert body["data"]["content"] == "Kilgore joined in 2020"
        assert body["data"]["embeds"][0]["description"] == "❌ Discarded."
        fields = {f["name"]: f["value"] for f in body["data"]["embeds"][0]["fields"]}
        assert fields["Section"] == "Band Members"
        assert fields["Submitted by"] == "killthrush"

    def test_discard_with_unparseable_message_falls_back_to_plain_content(
        self, mock_clients
    ):
        """If the embed can't be parsed anymore, discard still succeeds without crashing."""
        event = component_event("lore_discard", embeds=[])
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 7
        assert body["data"]["content"] == "❌ Discarded."
        assert body["data"]["embeds"] == []

    def test_confirm_defers_and_invokes_lore_worker(self, mock_clients, monkeypatch):
        """Confirm should defer the update and hand off to the async lore worker."""
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        event = component_event("lore_confirm", "Kilgore joined in 2020", lore_embed())

        with patch("src.handler.boto3.client") as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 6  # DEFERRED_UPDATE_MESSAGE

        assert mock_lambda_client.invoke.called
        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["source"] == "discord_lore_worker"
        assert payload["text"] == "Kilgore joined in 2020"
        assert payload["section"] == "Band Members"
        assert payload["submitted_by"] == "killthrush"
        assert payload["interaction_token"] == "interaction_token_abc"

    def test_confirm_resists_spoofed_delimiters_in_lore_text(
        self, mock_clients, monkeypatch
    ):
        """A user's own lore text containing fake field-like content shouldn't corrupt parsing.

        Since section is read from an exact embed field name and the lore text comes
        from message content verbatim (neither regexed out of a combined free-text
        blob), a lore submission that itself contains "Section: X" or "---" has no
        way to spoof what gets extracted.
        """
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        spoofy_text = (
            "Section: Hacked Section\n---\nActually this is still just lore text\n---\n"
        )
        event = component_event(
            "lore_confirm", spoofy_text, lore_embed(section="Band Members")
        )

        with patch("src.handler.boto3.client") as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            lambda_handler(event, None)

        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["section"] == "Band Members"
        assert payload["text"] == spoofy_text

    def test_confirm_with_unparseable_message(self, mock_clients):
        """Should warn instead of crashing if the message can't be parsed anymore."""
        event = component_event("lore_confirm", embeds=[])
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 7
        assert "try /lore again" in body["data"]["content"]

    def test_unknown_custom_id_returns_400(self, mock_clients):
        """Unrecognized custom_id should return an error, not silently succeed."""
        event = component_event(
            "something_else", "Kilgore joined in 2020", lore_embed()
        )
        response = lambda_handler(event, None)

        assert response["statusCode"] == 400


class TestLoreWorker:
    """Test the async worker that writes confirmed lore to the canon doc."""

    def test_writes_lore_and_reports_success(self, mock_clients):
        """The confirmed lore text should stay visible in the embed, not just a bare status line."""
        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_lore_worker",
                    "text": "Kilgore joined in 2020",
                    "section": "Band Members",
                    "submitted_by": "killthrush",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        mock_clients["docs"].append_to_section.assert_called_once_with(
            "Kilgore joined in 2020", "Band Members"
        )
        sent_json = mock_patch.call_args.kwargs["json"]
        assert sent_json["components"] == []
        assert sent_json["content"] == "Kilgore joined in 2020"
        assert "Band Members" in sent_json["embeds"][0]["description"]
        fields = {f["name"]: f["value"] for f in sent_json["embeds"][0]["fields"]}
        assert fields["Section"] == "Band Members"
        assert fields["Submitted by"] == "killthrush"

    def test_missing_submitted_by_defaults_to_unknown(self, mock_clients):
        """Older/malformed payloads without a submitter shouldn't crash the worker."""
        with patch("src.handler.requests.patch") as mock_patch:
            lambda_handler(
                {
                    "source": "discord_lore_worker",
                    "text": "Kilgore joined in 2020",
                    "section": "Band Members",
                    "interaction_token": "tok",
                },
                None,
            )

        sent_json = mock_patch.call_args.kwargs["json"]
        fields = {f["name"]: f["value"] for f in sent_json["embeds"][0]["fields"]}
        assert fields["Submitted by"] == "Unknown"

    def test_reports_failure_when_doc_write_fails(self, mock_clients):
        mock_clients["docs"].append_to_section.side_effect = Exception("API error")

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_lore_worker",
                    "text": "Kilgore joined in 2020",
                    "section": "Band Members",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        sent_json = mock_patch.call_args.kwargs["json"]
        assert "Failed to save" in sent_json["embeds"][0]["description"]

    def test_records_lore_submission_as_a_fact_on_success(self, mock_clients):
        with patch("src.handler.requests.patch"), patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            lambda_handler(
                {
                    "source": "discord_lore_worker",
                    "text": "Kilgore joined in 2020",
                    "section": "Band Members",
                    "submitted_by": "killthrush",
                    "interaction_token": "tok",
                },
                None,
            )

        mock_put_fact.assert_called_once_with(
            content="Kilgore joined in 2020",
            section_hint="Band Members",
            handle="killthrush",
            source="discord_lore",
            source_ref="tok",
            category="lore",
        )

    def test_fact_not_recorded_when_doc_write_fails(self, mock_clients):
        mock_clients["docs"].append_to_section.side_effect = Exception("API error")

        with patch("src.handler.requests.patch"), patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            lambda_handler(
                {
                    "source": "discord_lore_worker",
                    "text": "Kilgore joined in 2020",
                    "section": "Band Members",
                    "interaction_token": "tok",
                },
                None,
            )

        assert not mock_put_fact.called

    def test_fact_store_failure_does_not_affect_success_message(self, mock_clients):
        with patch("src.handler.requests.patch") as mock_patch, patch(
            "src.handler.fact_store.put_fact", side_effect=Exception("DynamoDB error")
        ):
            response = lambda_handler(
                {
                    "source": "discord_lore_worker",
                    "text": "Kilgore joined in 2020",
                    "section": "Band Members",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        sent_json = mock_patch.call_args.kwargs["json"]
        assert "Added to" in sent_json["embeds"][0]["description"]

    def test_missing_fields_skips_processing(self, mock_clients):
        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_lore_worker",
                    "text": None,
                    "section": "Band Members",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 400
        assert not mock_patch.called


def refresh_songs_event() -> dict:
    """Build a Discord APPLICATION_COMMAND event for /refresh-songs (no options)."""
    return {
        "headers": {},
        "body": json.dumps(
            {
                "type": 2,
                "token": "interaction_token_abc",
                "data": {"name": "refresh-songs", "options": []},
            }
        ),
    }


class TestSongRefreshDeferred:
    """/refresh-songs takes no text option, so it needs its own dispatch path."""

    def test_defers_and_invokes_worker(self, mock_clients, monkeypatch):
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        event = refresh_songs_event()

        with patch("src.handler.boto3.client") as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["source"] == "discord_song_refresh_worker"
        assert payload["interaction_token"] == "interaction_token_abc"


class TestSongRefreshWorker:
    """Test the async worker that enumerates Suno profiles and enqueues each song.

    Mining/classification/fact-writing now happens per-song in the SQS-triggered
    worker (see TestSongQueueWorker below), not here -- this worker only fans
    songs out to the queue and replies with a queued count.
    """

    def test_enqueues_every_clip_and_reports_summary(self, mock_clients):
        profiles = {
            "scuz_patrol": {
                "clips": [
                    {"id": "clip1", "title": "Incarcerator"},
                    {"id": "clip2", "title": "Track 9"},
                ]
            },
            "alfredokilgore": {"clips": [{"id": "clip3", "title": "Solo Cut"}]},
        }

        with patch(
            "src.handler.suno_client.fetch_profiles_parallel", return_value=profiles
        ), patch("src.handler.suno_client.enqueue_song") as mock_enqueue, patch(
            "src.handler.requests.patch"
        ) as mock_patch:
            response = lambda_handler(
                {"source": "discord_song_refresh_worker", "interaction_token": "tok"},
                None,
            )

        assert response["statusCode"] == 200
        assert mock_enqueue.call_count == 3
        mock_enqueue.assert_any_call("clip1", "scuz_patrol", "Incarcerator")
        mock_enqueue.assert_any_call("clip3", "alfredokilgore", "Solo Cut")
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "2 profile" in summary
        assert "queued 3 song" in summary

    def test_enqueues_songs_from_playlists_too(self, mock_clients):
        """A playlist can include songs posted under a different account
        than the one that curated it (features/collabs) -- these need to be
        discovered even though they'd never show up in PROFILE_HANDLES' own
        clips lists."""
        profiles = {
            "scuz_patrol": {
                "clips": [{"id": "clip1", "title": "Incarcerator"}],
                "playlists": [{"id": "playlist1", "name": "B-Sides", "song_count": 2}],
            },
        }
        playlist = {
            "name": "B-Sides",
            "playlist_clips": [
                {
                    "clip": {
                        "id": "clip2",
                        "handle": "metrivus",
                        "title": "Fiend Like Me",
                    }
                },
                {
                    "clip": {
                        "id": "clip3",
                        "handle": "lubonit84",
                        "title": "Still Here?",
                    }
                },
            ],
        }

        with patch(
            "src.handler.suno_client.fetch_profiles_parallel", return_value=profiles
        ), patch(
            "src.handler.suno_client.fetch_playlist", return_value=playlist
        ), patch(
            "src.handler.suno_client.enqueue_song"
        ) as mock_enqueue, patch(
            "src.handler.requests.patch"
        ) as mock_patch:
            response = lambda_handler(
                {"source": "discord_song_refresh_worker", "interaction_token": "tok"},
                None,
            )

        assert response["statusCode"] == 200
        assert mock_enqueue.call_count == 3
        mock_enqueue.assert_any_call("clip1", "scuz_patrol", "Incarcerator")
        mock_enqueue.assert_any_call("clip2", "metrivus", "Fiend Like Me")
        mock_enqueue.assert_any_call("clip3", "lubonit84", "Still Here?")
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "queued 3 song" in summary

    def test_playlist_fetch_failure_does_not_block_other_playlists(self, mock_clients):
        profiles = {
            "scuz_patrol": {
                "clips": [],
                "playlists": [
                    {"id": "bad_playlist", "name": "Broken"},
                    {"id": "good_playlist", "name": "OK"},
                ],
            },
        }
        good_playlist = {
            "name": "OK",
            "playlist_clips": [{"clip": {"id": "clip9", "handle": "metrivus"}}],
        }

        with patch(
            "src.handler.suno_client.fetch_profiles_parallel", return_value=profiles
        ), patch(
            "src.handler.suno_client.fetch_playlist",
            side_effect=[Exception("Suno API down"), good_playlist],
        ), patch(
            "src.handler.suno_client.enqueue_song"
        ) as mock_enqueue, patch(
            "src.handler.requests.patch"
        ) as mock_patch:
            response = lambda_handler(
                {"source": "discord_song_refresh_worker", "interaction_token": "tok"},
                None,
            )

        assert response["statusCode"] == 200
        mock_enqueue.assert_called_once_with("clip9", "metrivus", None)
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "queued 1 song" in summary

    def test_one_enqueue_failure_does_not_block_others(self, mock_clients):
        profiles = {
            "scuz_patrol": {
                "clips": [
                    {"id": "clip1", "title": "Incarcerator"},
                    {"id": "clip2", "title": "Track 9"},
                ]
            }
        }

        with patch(
            "src.handler.suno_client.fetch_profiles_parallel", return_value=profiles
        ), patch(
            "src.handler.suno_client.enqueue_song",
            side_effect=[Exception("SQS down"), None],
        ), patch(
            "src.handler.requests.patch"
        ) as mock_patch:
            response = lambda_handler(
                {"source": "discord_song_refresh_worker", "interaction_token": "tok"},
                None,
            )

        assert response["statusCode"] == 200
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "queued 1 song" in summary

    def test_profile_fetch_failure_sends_error_summary(self, mock_clients):
        with patch(
            "src.handler.suno_client.fetch_profiles_parallel",
            side_effect=Exception("Suno API down"),
        ), patch("src.handler.suno_client.enqueue_song") as mock_enqueue, patch(
            "src.handler.requests.patch"
        ) as mock_patch:
            response = lambda_handler(
                {"source": "discord_song_refresh_worker", "interaction_token": "tok"},
                None,
            )

        assert response["statusCode"] == 200
        assert not mock_enqueue.called
        content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Failed to check" in content

    def test_missing_interaction_token_returns_400(self, mock_clients):
        response = lambda_handler({"source": "discord_song_refresh_worker"}, None)
        assert response["statusCode"] == 400


def sqs_event(*bodies: dict) -> dict:
    """Build an SQS-triggered Lambda event with one Record per body."""
    return {"Records": [{"body": json.dumps(b)} for b in bodies]}


class TestSongQueueWorker:
    """Test the SQS-triggered worker that mines one song at a time for new facts.

    No Discord feedback here -- the queue worker only ever writes to the fact
    store (or skips), since SQS messages aren't tied to a live interaction.
    """

    def _clip(self, **overrides):
        clip = {
            "id": "clip1",
            "title": "Incarcerator",
            "handle": "scuz_patrol",
            "comment_count": 0,
            "caption": None,
            "metadata": {},
        }
        clip.update(overrides)
        return clip

    def test_new_lore_comment_reply_is_written_to_fact_store(
        self, mock_clients, monkeypatch
    ):
        monkeypatch.setenv("MANIFEST_BUCKET", "test-bucket")
        monkeypatch.setenv("FACTS_TABLE", "test-facts-table")
        mock_clients["claude"].suggest_section.return_value = "Band Members"
        mock_clients["docs"].read_document.return_value = "Canon doc"

        comments = {
            "results": [
                {
                    "id": "c1",
                    "content": "nice",
                    "replies": [
                        {
                            "id": "r1",
                            "user_handle": "alfredokilgore",
                            "content": "wrote this in prison",
                        }
                    ],
                }
            ]
        }

        with patch(
            "src.handler.suno_client.fetch_clip", return_value=self._clip()
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value=comments
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ) as mock_save_artifact, patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            response = lambda_handler(
                sqs_event({"clip_id": "clip1", "handle": "scuz_patrol"}), None
            )

        assert response["statusCode"] == 200
        mock_put_fact.assert_called_once()
        call_kwargs = mock_put_fact.call_args.kwargs
        assert call_kwargs["content"] == "wrote this in prison"
        assert call_kwargs["handle"] == "alfredokilgore"
        assert call_kwargs["source"] == "suno_reply"
        assert call_kwargs["source_ref"] == "r1"
        assert call_kwargs["section_hint"] == "Band Members"
        assert call_kwargs["category"] == "lore"
        assert call_kwargs["title"] == "Incarcerator"
        mock_save_artifact.assert_called_once()
        assert mock_save_artifact.call_args.args[0] == "clip1"
        # always_canon content never asks "is this lore?" -- classify_intent
        # is reserved for the gated (non-always_canon) path.
        assert not mock_clients["claude"].classify_intent.called

    def test_always_canon_content_still_written_if_section_suggestion_fails(
        self, mock_clients
    ):
        """alfredokilgore/scuz_patrol never speak out of character -- their
        words skip the lore/question/neither gate entirely, and even a
        failed section suggestion doesn't block the write."""
        mock_clients["claude"].suggest_section.side_effect = Exception("API error")
        mock_clients["docs"].read_document.return_value = "Canon doc"

        comments = {
            "results": [
                {
                    "id": "c1",
                    "content": "nice",
                    "replies": [
                        {
                            "id": "r1",
                            "user_handle": "alfredokilgore",
                            "content": "wrote this in prison",
                        }
                    ],
                }
            ]
        }

        with patch(
            "src.handler.suno_client.fetch_clip", return_value=self._clip()
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value=comments
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ), patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            lambda_handler(
                sqs_event({"clip_id": "clip1", "handle": "scuz_patrol"}), None
            )

        mock_put_fact.assert_called_once()
        assert mock_put_fact.call_args.kwargs["section_hint"] == "Unexplored Ideas"

    def test_new_caption_is_written_to_fact_store(self, mock_clients):
        mock_clients["claude"].suggest_section.return_value = "Band Chronology"
        mock_clients["docs"].read_document.return_value = "Canon doc"

        with patch(
            "src.handler.suno_client.fetch_clip",
            return_value=self._clip(caption="Wrote this after the breakup"),
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value={"results": []}
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ), patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            lambda_handler(
                sqs_event({"clip_id": "clip1", "handle": "scuz_patrol"}), None
            )

        call_kwargs = mock_put_fact.call_args.kwargs
        assert call_kwargs["content"] == "Wrote this after the breakup"
        assert call_kwargs["source"] == "suno_caption"
        assert call_kwargs["category"] == "lore"
        assert call_kwargs["title"] == "Incarcerator"

    def test_caption_mentioning_contributor_also_written_as_credit_fact(
        self, mock_clients
    ):
        mock_clients["claude"].suggest_section.return_value = "Virtual Discography"
        mock_clients["docs"].read_document.return_value = "Canon doc"

        with patch(
            "src.handler.suno_client.fetch_clip",
            return_value=self._clip(caption="Vocals by killthrush on this one"),
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value={"results": []}
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ), patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            lambda_handler(
                sqs_event({"clip_id": "clip1", "handle": "scuz_patrol"}), None
            )

        assert mock_put_fact.call_count == 2
        by_source = {c.kwargs["source"]: c.kwargs for c in mock_put_fact.call_args_list}
        assert by_source["suno_caption"]["category"] == "lore"
        assert by_source["suno_caption_credit"]["category"] == "credit"
        assert by_source["suno_caption_credit"]["section_hint"] == "Credits"
        assert "classification" not in by_source["suno_caption_credit"]

    def test_gated_reply_from_real_person_respects_classification(self, mock_clients):
        """metrivus/killthrush/lubonit84 aren't always in-character, so their
        replies still go through the lore/question/neither gate."""
        mock_clients["claude"].classify_intent.return_value = {"intent": "neither"}
        mock_clients["docs"].read_document.return_value = "Canon doc"

        comments = {
            "results": [
                {
                    "id": "c1",
                    "content": "nice",
                    "replies": [
                        {
                            "id": "r1",
                            "user_handle": "metrivus",
                            "content": "just chatting, not lore",
                        }
                    ],
                }
            ]
        }

        with patch(
            "src.handler.suno_client.fetch_clip", return_value=self._clip()
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value=comments
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ) as mock_save_artifact, patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            lambda_handler(
                sqs_event({"clip_id": "clip1", "handle": "scuz_patrol"}), None
            )

        assert not mock_put_fact.called
        # Artifact is still updated so an unchanged, already-rejected reply
        # isn't re-classified again on the next pass.
        assert mock_save_artifact.called

    def test_lyrics_candidate_is_written_without_classification(self, mock_clients):
        mock_clients["claude"].suggest_section.return_value = "Virtual Discography"
        mock_clients["docs"].read_document.return_value = "Canon doc"

        with patch(
            "src.handler.suno_client.fetch_clip",
            return_value=self._clip(
                metadata={"prompt": "Some backstory+×÷actual lyrics here"}
            ),
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value={"results": []}
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ), patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            lambda_handler(
                sqs_event({"clip_id": "clip1", "handle": "scuz_patrol"}), None
            )

        assert mock_put_fact.call_count == 2
        by_source = {c.kwargs["source"]: c.kwargs for c in mock_put_fact.call_args_list}
        assert by_source["suno_backstory"]["category"] == "lore"
        assert by_source["suno_backstory"]["content"] == "Some backstory"
        assert by_source["suno_lyrics_text"]["category"] == "lyrics"
        assert by_source["suno_lyrics_text"]["content"] == "actual lyrics here"
        assert by_source["suno_lyrics_text"]["section_hint"] == "Lyrics Archive"
        assert "classification" not in by_source["suno_lyrics_text"]
        # Only the backstory candidate needs a section suggestion -- lyrics
        # never do, and neither ever calls classify_intent (no lore gate for
        # always_canon content).
        mock_clients["claude"].suggest_section.assert_called_once_with(
            "Some backstory", "Canon doc"
        )
        assert not mock_clients["claude"].classify_intent.called

    def test_oversized_candidate_is_skipped_not_raised(self, mock_clients):
        from src.fact_store import MAX_FACT_LENGTH

        too_long = "x" * (MAX_FACT_LENGTH + 1)
        mock_clients["claude"].suggest_section.return_value = "Band Chronology"
        mock_clients["docs"].read_document.return_value = "Canon doc"

        with patch(
            "src.handler.suno_client.fetch_clip",
            return_value=self._clip(caption=too_long),
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value={"results": []}
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ), patch(
            "src.handler.fact_store.put_fact", side_effect=ValueError("too long")
        ):
            response = lambda_handler(
                sqs_event({"clip_id": "clip1", "handle": "scuz_patrol"}), None
            )

        assert response["statusCode"] == 200

    def test_no_candidates_still_saves_artifact_without_calling_claude(
        self, mock_clients
    ):
        with patch(
            "src.handler.suno_client.fetch_clip", return_value=self._clip()
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value={"results": []}
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ) as mock_save_artifact, patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            response = lambda_handler(
                sqs_event({"clip_id": "clip1", "handle": "scuz_patrol"}), None
            )

        assert response["statusCode"] == 200
        assert not mock_clients["claude_class"].called
        assert not mock_put_fact.called
        assert mock_save_artifact.called

    def test_processes_each_record_in_a_batch_independently(self, mock_clients):
        mock_clients["claude"].suggest_section.return_value = "Band Chronology"
        mock_clients["docs"].read_document.return_value = "Canon doc"

        with patch(
            "src.handler.suno_client.fetch_clip",
            side_effect=lambda clip_id: self._clip(
                id=clip_id, caption=f"caption-{clip_id}"
            ),
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value={"results": []}
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ) as mock_save_artifact, patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
            response = lambda_handler(
                sqs_event(
                    {"clip_id": "clip1", "handle": "a"},
                    {"clip_id": "clip2", "handle": "b"},
                ),
                None,
            )

        assert response["statusCode"] == 200
        assert mock_save_artifact.call_count == 2
        assert mock_put_fact.call_count == 2


class TestReconstructHandler:
    """Test the debounced reconstruction Lambda: one-shot doc synthesis from facts."""

    def _lore_fact(self, fact_id, **overrides):
        fact = {
            "fact_id": fact_id,
            "content": "Kilgore joined in 2020",
            "section_hint": "Band Members",
            "category": "lore",
        }
        fact.update(overrides)
        return fact

    def test_no_pending_facts_is_a_noop(self, mock_clients):
        with patch("src.handler.fact_store.get_pending_facts", return_value=[]):
            response = reconstruct_handler({}, None)

        assert response["statusCode"] == 200
        assert not mock_clients["docs_class"].called
        assert not mock_clients["claude_class"].called

    def test_only_non_lore_pending_facts_marks_integrated_without_synthesis(
        self, mock_clients
    ):
        pending = [
            {"fact_id": "f1", "content": "lyrics", "category": "lyrics"},
            {"fact_id": "f2", "content": "credit", "category": "credit"},
        ]

        with patch(
            "src.handler.fact_store.get_pending_facts", return_value=pending
        ), patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction"
        ) as mock_get_lore, patch(
            "src.handler.fact_store.mark_integrated"
        ) as mock_mark_integrated:
            response = reconstruct_handler({}, None)

        assert response["statusCode"] == 200
        assert mock_mark_integrated.call_count == 2
        assert not mock_get_lore.called
        assert not mock_clients["docs_class"].called
        assert not mock_clients["claude_class"].called

    def test_successful_synthesis_writes_sections_and_marks_facts_integrated(
        self, mock_clients
    ):
        pending = [self._lore_fact("f1"), self._lore_fact("f2")]
        mock_clients["docs"].read_document.return_value = "current doc text"
        mock_clients["claude"].synthesize_doc.return_value = {
            "doc_sections": [{"name": "Band Members", "content": "new body text"}],
            "fact_accounting": [
                {"fact_id": "f1", "status": "included", "section": "Band Members"},
                {"fact_id": "f2", "status": "already_present", "section": None},
            ],
        }

        with patch(
            "src.handler.fact_store.get_pending_facts", return_value=pending
        ), patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction",
            return_value=pending,
        ), patch(
            "src.handler.fact_store.mark_integrated"
        ) as mock_mark_integrated:
            response = reconstruct_handler({}, None)

        assert response["statusCode"] == 200
        mock_clients["docs"].replace_section_content.assert_called_once_with(
            "Band Members", "new body text"
        )
        assert mock_mark_integrated.call_count == 2
        integrated_ids = {c.args[0] for c in mock_mark_integrated.call_args_list}
        assert integrated_ids == {"f1", "f2"}

    def test_fact_missing_from_accounting_stays_pending(self, mock_clients):
        pending = [self._lore_fact("f1"), self._lore_fact("f2")]
        mock_clients["docs"].read_document.return_value = "current doc text"
        mock_clients["claude"].synthesize_doc.return_value = {
            "doc_sections": [{"name": "Band Members", "content": "new body text"}],
            "fact_accounting": [
                {"fact_id": "f1", "status": "included", "section": "Band Members"},
                # f2 is silently missing from the accounting.
            ],
        }

        with patch(
            "src.handler.fact_store.get_pending_facts", return_value=pending
        ), patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction",
            return_value=pending,
        ), patch(
            "src.handler.fact_store.mark_integrated"
        ) as mock_mark_integrated:
            response = reconstruct_handler({}, None)

        assert response["statusCode"] == 200
        mock_mark_integrated.assert_called_once()
        assert mock_mark_integrated.call_args.args[0] == "f1"

    def test_fact_accounted_for_a_section_that_failed_to_write_stays_pending(
        self, mock_clients
    ):
        pending = [self._lore_fact("f1"), self._lore_fact("f2")]
        mock_clients["docs"].read_document.return_value = "current doc text"
        mock_clients["docs"].replace_section_content.side_effect = Exception(
            "Docs API error"
        )
        mock_clients["claude"].synthesize_doc.return_value = {
            "doc_sections": [{"name": "Band Members", "content": "new body text"}],
            "fact_accounting": [
                {"fact_id": "f1", "status": "included", "section": "Band Members"},
                {"fact_id": "f2", "status": "already_present", "section": None},
            ],
        }

        with patch(
            "src.handler.fact_store.get_pending_facts", return_value=pending
        ), patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction",
            return_value=pending,
        ), patch(
            "src.handler.fact_store.mark_integrated"
        ) as mock_mark_integrated:
            response = reconstruct_handler({}, None)

        assert response["statusCode"] == 200
        # f1's section failed to write -- stays pending. f2 was
        # "already_present" (no section write needed) -- still integrated.
        mock_mark_integrated.assert_called_once()
        assert mock_mark_integrated.call_args.args[0] == "f2"

    def test_synthesis_failure_leaves_all_facts_pending(self, mock_clients):
        pending = [self._lore_fact("f1")]
        mock_clients["docs"].read_document.return_value = "current doc text"
        mock_clients["claude"].synthesize_doc.side_effect = Exception("API error")

        with patch(
            "src.handler.fact_store.get_pending_facts", return_value=pending
        ), patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction",
            return_value=pending,
        ), patch(
            "src.handler.fact_store.mark_integrated"
        ) as mock_mark_integrated:
            response = reconstruct_handler({}, None)

        assert response["statusCode"] == 200
        assert not mock_mark_integrated.called
        assert not mock_clients["docs"].replace_section_content.called

    def test_read_document_failure_leaves_all_facts_pending(self, mock_clients):
        pending = [self._lore_fact("f1")]
        mock_clients["docs"].read_document.side_effect = Exception("Docs API error")

        with patch(
            "src.handler.fact_store.get_pending_facts", return_value=pending
        ), patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction",
            return_value=pending,
        ), patch(
            "src.handler.fact_store.mark_integrated"
        ) as mock_mark_integrated:
            response = reconstruct_handler({}, None)

        assert response["statusCode"] == 200
        assert not mock_mark_integrated.called
        assert not mock_clients["claude"].synthesize_doc.called


def calibration_command_event() -> dict:
    """Build a Discord APPLICATION_COMMAND event for /test (no options)."""
    return {
        "headers": {},
        "body": json.dumps(
            {
                "type": 2,
                "token": "interaction_token_abc",
                "data": {"name": "test", "options": []},
            }
        ),
    }


class TestCalibrationTestDeferred:
    """/test takes no text option, so it needs its own dispatch path."""

    def test_defers_and_invokes_worker(self, mock_clients, monkeypatch):
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        event = calibration_command_event()

        with patch("src.handler.boto3.client") as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["source"] == "discord_test_worker"
        assert payload["interaction_token"] == "interaction_token_abc"


class TestCalibrationTestWorker:
    """Test the async worker that runs calibration queries and grades them."""

    FAKE_QUERIES = [
        {
            "query_id": "q1",
            "category": "member_bio",
            "question": "Who is Kilgore?",
            "required_fact_ids": ["f1", "f2"],
            "speculative": False,
        },
        {
            "query_id": "q2",
            "category": "chronology",
            "question": "What happened in 2055?",
            "required_fact_ids": ["f3"],
            "speculative": False,
        },
    ]

    def _facts(self):
        return [
            {
                "fact_id": "f1",
                "content": "Kilgore joined the band",
                "handle": "scuz_patrol",
                "section_hint": "Band Members",
            },
            {
                "fact_id": "f2",
                "content": "Kilgore is a front-mutant",
                "handle": "scuz_patrol",
                "section_hint": "Band Members",
            },
            {
                "fact_id": "f3",
                "content": "Mars was colonized in 2055",
                "handle": "scuz_patrol",
                "section_hint": "Band Chronology",
            },
        ]

    def test_computes_overall_score_and_reports_summary(self, mock_clients):
        mock_clients["claude"].answer_question_from_facts.return_value = "An answer."
        mock_clients["claude"].grade_answer_coverage.side_effect = [
            {"score": 1.0, "results": []},
            {"score": 0.0, "results": []},
        ]

        with patch("src.handler.CALIBRATION_QUERIES", self.FAKE_QUERIES), patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction",
            return_value=self._facts(),
        ), patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {"source": "discord_test_worker", "interaction_token": "tok"}, None
            )

        assert response["statusCode"] == 200
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "q1" in summary and "100%" in summary
        assert "q2" in summary and "0%" in summary
        assert "50%" in summary  # overall = mean of 1.0 and 0.0

    def test_missing_required_fact_is_noted_but_does_not_crash(self, mock_clients):
        queries = [
            {
                "query_id": "q_missing",
                "category": "member_bio",
                "question": "Who is Kilgore?",
                "required_fact_ids": ["f1", "does-not-exist"],
                "speculative": False,
            }
        ]
        mock_clients["claude"].answer_question_from_facts.return_value = "An answer."
        mock_clients["claude"].grade_answer_coverage.return_value = {
            "score": 1.0,
            "results": [],
        }

        with patch("src.handler.CALIBRATION_QUERIES", queries), patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction",
            return_value=self._facts(),
        ), patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {"source": "discord_test_worker", "interaction_token": "tok"}, None
            )

        assert response["statusCode"] == 200
        # grade_answer_coverage should only get the fact that actually exists
        graded_facts = mock_clients["claude"].grade_answer_coverage.call_args.args[2]
        assert len(graded_facts) == 1
        assert graded_facts[0]["fact_id"] == "f1"
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "not found in store" in summary

    def test_fact_load_failure_sends_error_message(self, mock_clients):
        with patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction",
            side_effect=Exception("DynamoDB down"),
        ), patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {"source": "discord_test_worker", "interaction_token": "tok"}, None
            )

        assert response["statusCode"] == 200
        content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Failed to load facts" in content

    def test_one_query_failure_does_not_block_others(self, mock_clients):
        mock_clients["claude"].answer_question_from_facts.side_effect = [
            Exception("Claude API down"),
            "An answer.",
        ]
        mock_clients["claude"].grade_answer_coverage.return_value = {
            "score": 1.0,
            "results": [],
        }

        with patch("src.handler.CALIBRATION_QUERIES", self.FAKE_QUERIES), patch(
            "src.handler.fact_store.get_lore_facts_for_reconstruction",
            return_value=self._facts(),
        ), patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {"source": "discord_test_worker", "interaction_token": "tok"}, None
            )

        assert response["statusCode"] == 200
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "q1" in summary and "failed" in summary
        assert "q2" in summary and "100%" in summary

    def test_missing_interaction_token_returns_400(self, mock_clients):
        response = lambda_handler({"source": "discord_test_worker"}, None)
        assert response["statusCode"] == 400


class TestHandlerErrorHandling:
    """Test error handling."""

    def test_invalid_event_json(self, mock_clients):
        """Should handle invalid JSON in event body gracefully."""
        event = {"body": "not valid json"}
        response = lambda_handler(event, None)
        # Invalid JSON results in unknown event type, which returns 400 (no message)
        assert response["statusCode"] == 400

    def test_missing_body(self, mock_clients):
        """Should handle missing body in event."""
        event = {}
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400

    def test_returns_json_response(self, mock_clients):
        """Should always return valid JSON."""
        event = {"body": json.dumps({"type": 1, "data": {}})}
        response = lambda_handler(event, None)
        assert "statusCode" in response
        assert "body" in response
        # Verify body is valid JSON
        json.loads(response["body"])
