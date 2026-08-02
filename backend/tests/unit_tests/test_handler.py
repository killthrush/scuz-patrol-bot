"""Unit tests for Lambda handler."""

import json
import pytest
from unittest.mock import Mock, patch
from src.handler import lambda_handler


@pytest.fixture
def mock_clients():
    """Mock all external clients."""
    with patch("src.handler.ClaudeClient") as mock_claude:
        mock_claude_instance = Mock()
        mock_claude.return_value = mock_claude_instance
        yield {
            "claude": mock_claude_instance,
            "claude_class": mock_claude,
        }


def command_event(
    command_name: str,
    option_name: str = None,
    option_value: str = None,
    user_name: str = "killthrush",
) -> dict:
    """Build a Discord APPLICATION_COMMAND event, optionally with one string option."""
    options = (
        [{"type": 3, "name": option_name, "value": option_value}]
        if option_name is not None
        else []
    )
    return {
        "headers": {},
        "body": json.dumps(
            {
                "type": 2,
                "token": "interaction_token_abc",
                "member": {"user": {"id": "123", "username": user_name}},
                "data": {"name": command_name, "options": options},
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

    def test_defers_response_for_ask(self, mock_clients):
        """A valid /ask command should get an immediate deferred ack, not the answer."""
        event = command_event("ask", "question", "Who is Alfredo?")
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

    def test_defer_threads_submitter_username_to_lore_intake_worker(
        self, mock_clients, monkeypatch
    ):
        """The command invoker's username should flow through to the lore
        intake worker payload -- that's the only way a later lore
        confirmation can record who actually wrote the lore."""
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        event = command_event(
            "lore", "content", "Kilgore joined in 2020", user_name="metrivus"
        )

        with patch("src.handler.boto3.client") as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            lambda_handler(event, None)

        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["source"] == "discord_lore_intake_worker"
        assert payload["user_name"] == "metrivus"

    def test_missing_message_returns_400(self, mock_clients):
        """A known command with no text option should 400 without deferring."""
        event = command_event("ask")
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "error" in body

    def test_unknown_command_returns_400(self, mock_clients):
        """An unrecognized command name should 400, not silently fall through."""
        event = {"body": json.dumps({"type": 2, "data": {}})}
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "error" in body


class TestLoreAuthorization:
    """Only LORE_SUBMITTERS may submit lore -- everyone else gets an
    immediate rejection, not a deferred prompt."""

    def test_allowed_submitter_gets_deferred(self, mock_clients, monkeypatch):
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        event = command_event(
            "lore", "content", "Kilgore joined in 2020", user_name="metrivus"
        )

        with patch("src.handler.boto3.client") as mock_boto_client:
            mock_boto_client.return_value = Mock()
            response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

    def test_allowed_submitter_is_case_insensitive(self, mock_clients, monkeypatch):
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        event = command_event(
            "lore", "content", "Kilgore joined in 2020", user_name="Synthypixie"
        )

        with patch("src.handler.boto3.client") as mock_boto_client:
            mock_boto_client.return_value = Mock()
            response = lambda_handler(event, None)

        body = json.loads(response["body"])
        assert body["type"] == 5

    def test_unauthorized_submitter_is_rejected_immediately(self, mock_clients):
        event = command_event(
            "lore", "content", "Kilgore joined in 2020", user_name="random_fan"
        )

        with patch("src.handler.boto3.client") as mock_boto_client:
            response = lambda_handler(event, None)

        assert not mock_boto_client.called  # no self-invoke, nothing deferred
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 4  # CHANNEL_MESSAGE_WITH_SOURCE (immediate)
        assert "Only certain contributors" in body["data"]["content"]


class TestLoreIntakeWorker:
    """Test the async worker that builds /lore's confirm/discard prompt.

    /lore already declares its text as lore by virtue of being /lore --
    unlike the old shared /ask+/lore pipeline, there's no lore/question/
    neither gate here, only "which section" (suggest_section).
    """

    def intake_event(
        self,
        text="Kilgore joined in 2020",
        interaction_token="interaction_token_abc",
        user_name="killthrush",
    ):
        return {
            "source": "discord_lore_intake_worker",
            "text": text,
            "interaction_token": interaction_token,
            "user_name": user_name,
        }

    def test_sends_confirm_prompt_with_suggested_section(self, mock_clients):
        mock_clients["claude"].suggest_section.return_value = "Band Members"

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(self.intake_event(), None)

        assert response["statusCode"] == 200
        mock_clients["claude"].suggest_section.assert_called_once_with(
            "Kilgore joined in 2020"
        )
        sent_json = mock_patch.call_args.kwargs["json"]
        assert sent_json["content"] == ""
        assert sent_json["embeds"][0]["description"] == "Kilgore joined in 2020"
        fields = {f["name"]: f["value"] for f in sent_json["embeds"][0]["fields"]}
        assert fields["Section"] == "Band Members"
        assert fields["Submitted by"] == "killthrush"
        custom_ids = {
            button["custom_id"]
            for row in sent_json["components"]
            for button in row["components"]
        }
        assert custom_ids == {"lore_confirm", "lore_discard"}

    def test_over_length_cap_is_rejected(self, mock_clients):
        """A lore submission over the Discord embed limit should get an
        error, not a confirm prompt -- this is bounded by
        DISCORD_EMBED_DESCRIPTION_LIMIT, not fact_store.MAX_FACT_LENGTH,
        since the confirmation prompt has to fit in one Discord embed."""
        from src.handler import DISCORD_EMBED_DESCRIPTION_LIMIT

        too_long = "x" * (DISCORD_EMBED_DESCRIPTION_LIMIT + 1)

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(self.intake_event(text=too_long), None)

        assert response["statusCode"] == 200
        sent_json = mock_patch.call_args.kwargs["json"]
        assert str(DISCORD_EMBED_DESCRIPTION_LIMIT) in sent_json["content"]
        assert sent_json.get("components") is None
        assert sent_json.get("embeds") is None
        assert not mock_clients["claude"].suggest_section.called

    def test_section_suggestion_failure_falls_back_to_unexplored_ideas(
        self, mock_clients
    ):
        mock_clients["claude"].suggest_section.side_effect = Exception("API error")

        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(self.intake_event(), None)

        assert response["statusCode"] == 200
        sent_json = mock_patch.call_args.kwargs["json"]
        fields = {f["name"]: f["value"] for f in sent_json["embeds"][0]["fields"]}
        assert fields["Section"] == "Unexplored Ideas"

    def test_missing_text_or_token_returns_400(self, mock_clients):
        with patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(self.intake_event(text=None), None)

        assert response["statusCode"] == 400
        assert not mock_patch.called


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


def lore_embed(
    text: str = "Kilgore joined in 2020",
    section: str = "Band Members",
    submitted_by: str = "killthrush",
) -> list:
    """Build the embed structure a pending lore confirmation message carries.

    The lore text lives in the embed description (see
    _build_lore_confirmation_message), not the message content.
    """
    return [
        {
            "description": text,
            "fields": [
                {"name": "Section", "value": section},
                {"name": "Submitted by", "value": submitted_by},
            ],
        }
    ]


class TestComponentInteraction:
    """Test Confirm/Discard button clicks on a pending lore submission."""

    def test_discard_updates_message_immediately(self, mock_clients):
        """Discard should synchronously update the message, no async work needed.

        The original lore text stays visible (in the embed description) so
        people scrolling back can see what was discarded, instead of a bare
        status line.
        """
        event = component_event(
            "lore_discard", embeds=lore_embed("Kilgore joined in 2020")
        )
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 7  # UPDATE_MESSAGE
        assert body["data"]["components"] == []
        assert body["data"]["embeds"][0]["description"] == "Kilgore joined in 2020"
        assert "❌ Discarded." in body["data"]["embeds"][0]["title"]
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
        event = component_event(
            "lore_confirm", embeds=lore_embed("Kilgore joined in 2020")
        )

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
        from the embed description verbatim (neither regexed out of a combined
        free-text blob), a lore submission that itself contains "Section: X" or
        "---" has no way to spoof what gets extracted.
        """
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        spoofy_text = (
            "Section: Hacked Section\n---\nActually this is still just lore text\n---\n"
        )
        event = component_event(
            "lore_confirm", embeds=lore_embed(spoofy_text, section="Band Members")
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
            "something_else", embeds=lore_embed("Kilgore joined in 2020")
        )
        response = lambda_handler(event, None)

        assert response["statusCode"] == 400


class TestLoreWorker:
    """Test the async worker that records confirmed lore as a fact."""

    def test_writes_lore_and_reports_success(self, mock_clients):
        """The confirmed lore text should stay visible in the embed, not just a bare status line."""
        with patch("src.handler.requests.patch") as mock_patch, patch(
            "src.handler.fact_store.put_fact"
        ) as mock_put_fact:
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
        mock_put_fact.assert_called_once_with(
            content="Kilgore joined in 2020",
            section_hint="Band Members",
            handle="killthrush",
            source="discord_lore",
            source_ref="tok",
            category="lore",
        )
        sent_json = mock_patch.call_args.kwargs["json"]
        assert sent_json["components"] == []
        assert sent_json["content"] == ""
        assert sent_json["embeds"][0]["description"] == "Kilgore joined in 2020"
        assert "Recorded under" in sent_json["embeds"][0]["title"]
        assert "Band Members" in sent_json["embeds"][0]["title"]
        fields = {f["name"]: f["value"] for f in sent_json["embeds"][0]["fields"]}
        assert fields["Section"] == "Band Members"
        assert fields["Submitted by"] == "killthrush"

    def test_missing_submitted_by_defaults_to_unknown(self, mock_clients):
        """Older/malformed payloads without a submitter shouldn't crash the worker."""
        with patch("src.handler.requests.patch") as mock_patch, patch(
            "src.handler.fact_store.put_fact"
        ):
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

    def test_reports_failure_when_fact_store_write_fails(self, mock_clients):
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
        assert "Failed to save" in sent_json["embeds"][0]["title"]

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


def refresh_songs_event(channel_id: str = "channel_456") -> dict:
    """Build a Discord APPLICATION_COMMAND event for /refresh-songs (no options)."""
    return {
        "headers": {},
        "body": json.dumps(
            {
                "type": 2,
                "token": "interaction_token_abc",
                "channel_id": channel_id,
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
        assert payload["channel_id"] == "channel_456"


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
        mock_enqueue.assert_any_call("clip1", "scuz_patrol", "Incarcerator", None)
        mock_enqueue.assert_any_call("clip3", "alfredokilgore", "Solo Cut", None)
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "2 profile" in summary
        assert "queued 3 song" in summary.lower()
        assert "scuz_patrol" in summary and "2 song" in summary
        assert "alfredokilgore" in summary and "1 song" in summary

    def test_threads_channel_id_to_enqueued_songs(self, mock_clients):
        """channel_id lets the per-song worker post mining results back once
        it's done, well after this interaction's own token may have expired."""
        profiles = {
            "scuz_patrol": {"clips": [{"id": "clip1", "title": "Incarcerator"}]}
        }

        with patch(
            "src.handler.suno_client.fetch_profiles_parallel", return_value=profiles
        ), patch("src.handler.suno_client.enqueue_song") as mock_enqueue, patch(
            "src.handler.requests.patch"
        ):
            lambda_handler(
                {
                    "source": "discord_song_refresh_worker",
                    "interaction_token": "tok",
                    "channel_id": "channel_456",
                },
                None,
            )

        mock_enqueue.assert_called_once_with(
            "clip1", "scuz_patrol", "Incarcerator", "channel_456"
        )

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
        mock_enqueue.assert_any_call("clip1", "scuz_patrol", "Incarcerator", None)
        mock_enqueue.assert_any_call("clip2", "metrivus", "Fiend Like Me", None)
        mock_enqueue.assert_any_call("clip3", "lubonit84", "Still Here?", None)
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "queued 3 song" in summary.lower()
        assert "+2 via playlists" in summary

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
        mock_enqueue.assert_called_once_with("clip9", "metrivus", None, None)
        summary = mock_patch.call_args.kwargs["json"]["content"]
        assert "queued 1 song" in summary.lower()

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
        assert "queued 1 song" in summary.lower()

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

    Posts a channel message (not an interaction follow-up, since SQS
    messages aren't tied to a live interaction) when a song's mining pass
    actually writes any facts -- see TestSongQueueWorkerChannelNotification.
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
        mock_clients["claude"].suggest_section.assert_called_once_with("Some backstory")
        assert not mock_clients["claude"].classify_intent.called

    def test_oversized_candidate_is_skipped_not_raised(self, mock_clients):
        from src.fact_store import MAX_FACT_LENGTH

        too_long = "x" * (MAX_FACT_LENGTH + 1)
        mock_clients["claude"].suggest_section.return_value = "Band Chronology"

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


class TestSongQueueWorkerChannelNotification:
    """channel_id (threaded through from /refresh-songs) triggers a direct
    channel post reporting how many facts a song's mining pass wrote --
    posted via the bot token, not an interaction follow-up, since a song's
    turn in the FIFO queue can come up well after the original interaction
    token would have expired."""

    def _clip(self, **overrides):
        clip = {
            "id": "clip1",
            "title": "Incarcerator",
            "handle": "scuz_patrol",
            "comment_count": 0,
            "caption": "Wrote this after the breakup",
            "metadata": {},
        }
        clip.update(overrides)
        return clip

    def test_posts_channel_message_when_facts_are_written(self, mock_clients):
        mock_clients["claude"].suggest_section.return_value = "Band Chronology"

        with patch(
            "src.handler.suno_client.fetch_clip", return_value=self._clip()
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value={"results": []}
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ), patch(
            "src.handler.fact_store.put_fact"
        ), patch(
            "src.handler.requests.post"
        ) as mock_post:
            response = lambda_handler(
                sqs_event(
                    {
                        "clip_id": "clip1",
                        "handle": "scuz_patrol",
                        "title": "Incarcerator",
                        "channel_id": "channel_456",
                    }
                ),
                None,
            )

        assert response["statusCode"] == 200
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert (
            call_args.args[0]
            == "https://discord.com/api/v10/channels/channel_456/messages"
        )
        assert "Incarcerator" in call_args.kwargs["json"]["content"]
        assert "1 new fact" in call_args.kwargs["json"]["content"]

    def test_no_channel_message_when_no_channel_id(self, mock_clients):
        """Old queued messages (from before this feature) have no channel_id
        -- silently skip notification instead of erroring."""
        mock_clients["claude"].suggest_section.return_value = "Band Chronology"

        with patch(
            "src.handler.suno_client.fetch_clip", return_value=self._clip()
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value={"results": []}
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ), patch(
            "src.handler.fact_store.put_fact"
        ), patch(
            "src.handler.requests.post"
        ) as mock_post:
            lambda_handler(
                sqs_event({"clip_id": "clip1", "handle": "scuz_patrol"}), None
            )

        assert not mock_post.called

    def test_no_channel_message_when_no_facts_written(self, mock_clients):
        """A song that yields nothing new shouldn't spam the channel."""
        with patch(
            "src.handler.suno_client.fetch_clip",
            return_value=self._clip(caption=None),
        ), patch(
            "src.handler.suno_client.fetch_comments", return_value={"results": []}
        ), patch(
            "src.handler.suno_client.load_song_artifact", return_value={}
        ), patch(
            "src.handler.suno_client.save_song_artifact"
        ), patch(
            "src.handler.requests.post"
        ) as mock_post:
            lambda_handler(
                sqs_event(
                    {
                        "clip_id": "clip1",
                        "handle": "scuz_patrol",
                        "channel_id": "channel_456",
                    }
                ),
                None,
            )

        assert not mock_post.called


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
            "src.handler.fact_store.get_lore_facts",
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
            "src.handler.fact_store.get_lore_facts",
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
            "src.handler.fact_store.get_lore_facts",
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
            "src.handler.fact_store.get_lore_facts",
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


def ask_command_event(question: str = "Who is Alfredo Kilgore?") -> dict:
    """Build a Discord APPLICATION_COMMAND event for /ask."""
    return {
        "headers": {},
        "body": json.dumps(
            {
                "type": 2,
                "token": "interaction_token_abc",
                "data": {
                    "name": "ask",
                    "options": [{"type": 3, "name": "question", "value": question}],
                },
            }
        ),
    }


class TestAskDeferred:
    """/ask answers directly from the fact store -- verify it defers
    immediately and hands off to the async ask worker."""

    def test_defers_and_invokes_worker(self, mock_clients, monkeypatch):
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scuz-patrol-bot-dev")
        event = ask_command_event("Who is Alfredo Kilgore?")

        with patch("src.handler.boto3.client") as mock_boto_client:
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["type"] == 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

        payload = json.loads(mock_lambda_client.invoke.call_args.kwargs["Payload"])
        assert payload["source"] == "discord_ask_worker"
        assert payload["message"] == "Who is Alfredo Kilgore?"
        assert payload["interaction_token"] == "interaction_token_abc"

    def test_missing_question_returns_400(self, mock_clients):
        event = {
            "headers": {},
            "body": json.dumps(
                {"type": 2, "token": "tok", "data": {"name": "ask", "options": []}}
            ),
        }
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400


class TestAskWorker:
    """Test the async worker backing /ask."""

    def _facts(self):
        return [
            {
                "fact_id": "f1",
                "content": "Kilgore is a front-mutant",
                "handle": "scuz_patrol",
                "section_hint": "Band Members",
            }
        ]

    def test_answers_from_facts_and_reports(self, mock_clients):
        mock_clients[
            "claude"
        ].answer_question_from_facts.return_value = "Kilgore is the front-mutant."

        with patch(
            "src.handler.fact_store.get_lore_facts",
            return_value=self._facts(),
        ), patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_ask_worker",
                    "message": "Who is Kilgore?",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        mock_clients["claude"].answer_question_from_facts.assert_called_once_with(
            "Who is Kilgore?", self._facts()
        )
        sent = mock_patch.call_args.kwargs["json"]
        assert "Who is Kilgore?" in sent["content"]
        assert sent["embeds"][0]["description"] == "Kilgore is the front-mutant."

    def test_long_answer_is_sent_as_embed_not_content(self, mock_clients):
        """Discord caps plain content at 2000 chars; a full answer must go in
        the embed description (4096 cap), not get crammed into content."""
        long_answer = "x" * 3000
        mock_clients["claude"].answer_question_from_facts.return_value = long_answer

        with patch(
            "src.handler.fact_store.get_lore_facts",
            return_value=self._facts(),
        ), patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_ask_worker",
                    "message": "Who is Kilgore?",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        sent = mock_patch.call_args.kwargs["json"]
        assert len(sent["content"]) < 2000
        assert sent["embeds"][0]["description"] == long_answer

    def test_answer_longer_than_embed_limit_is_truncated(self, mock_clients):
        mock_clients["claude"].answer_question_from_facts.return_value = "x" * 5000

        with patch(
            "src.handler.fact_store.get_lore_facts",
            return_value=self._facts(),
        ), patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_ask_worker",
                    "message": "Who is Kilgore?",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        description = mock_patch.call_args.kwargs["json"]["embeds"][0]["description"]
        assert len(description) == 4096
        assert description.endswith("…")

    def test_fact_load_failure_sends_error_message(self, mock_clients):
        with patch(
            "src.handler.fact_store.get_lore_facts",
            side_effect=Exception("DynamoDB down"),
        ), patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_ask_worker",
                    "message": "Who is Kilgore?",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Failed to generate an answer" in content

    def test_answer_failure_sends_error_message(self, mock_clients):
        mock_clients["claude"].answer_question_from_facts.side_effect = Exception(
            "Claude API down"
        )

        with patch(
            "src.handler.fact_store.get_lore_facts",
            return_value=self._facts(),
        ), patch("src.handler.requests.patch") as mock_patch:
            response = lambda_handler(
                {
                    "source": "discord_ask_worker",
                    "message": "Who is Kilgore?",
                    "interaction_token": "tok",
                },
                None,
            )

        assert response["statusCode"] == 200
        content = mock_patch.call_args.kwargs["json"]["content"]
        assert "Failed to generate an answer" in content

    def test_missing_interaction_token_returns_400(self, mock_clients):
        response = lambda_handler(
            {"source": "discord_ask_worker", "message": "Who is Kilgore?"}, None
        )
        assert response["statusCode"] == 400

    def test_missing_message_returns_400(self, mock_clients):
        response = lambda_handler(
            {"source": "discord_ask_worker", "interaction_token": "tok"}, None
        )
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
