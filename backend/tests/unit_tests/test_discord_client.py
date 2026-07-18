"""Unit tests for Discord webhook parsing."""

import json
from src.discord_client import parse_discord_event, extract_message_from_event


class TestParseDiscordEvent:
    """Test Discord event parsing."""

    def test_parses_ping_challenge(self):
        """INTERACTION_PING (type=1) should return ping type."""
        event = {
            "headers": {},
            "body": json.dumps({"type": 1}),
        }
        parsed = parse_discord_event(event)

        assert parsed["type"] == "ping"
        assert "respond_with" in parsed

    def test_parses_command_event(self):
        """APPLICATION_COMMAND (type=2) should return command type."""
        event = {
            "headers": {},
            "body": json.dumps({
                "type": 2,
                "id": "interaction_123",
                "token": "token_abc",
                "guild_id": "guild_456",
                "channel_id": "channel_789",
                "member": {"user": {"id": "user_111", "username": "testuser"}},
                "data": {"name": "ask", "options": []},
            }),
        }
        parsed = parse_discord_event(event)

        assert parsed["type"] == "command"
        assert parsed["interaction_id"] == "interaction_123"
        assert parsed["user_name"] == "testuser"

    def test_extracts_user_details(self):
        """Should extract all user and guild information."""
        event = {
            "headers": {},
            "body": json.dumps({
                "type": 2,
                "id": "int_1",
                "token": "tok_1",
                "guild_id": "guild_123",
                "channel_id": "channel_456",
                "member": {"user": {"id": "user_789", "username": "alice"}},
                "data": {"name": "cmd", "options": []},
            }),
        }
        parsed = parse_discord_event(event)

        assert parsed["guild_id"] == "guild_123"
        assert parsed["channel_id"] == "channel_456"
        assert parsed["user_id"] == "user_789"
        assert parsed["user_name"] == "alice"


class TestExtractMessageFromEvent:
    """Test message extraction from events."""

    def test_extracts_string_option(self):
        """Extract message from STRING type option."""
        event = {
            "type": "command",
            "command_options": [
                {"type": 3, "name": "question", "value": "What is Scuz?"}
            ],
        }
        message = extract_message_from_event(event)

        assert message == "What is Scuz?"

    def test_ignores_non_string_options(self):
        """Only extract STRING type (type=3) options."""
        event = {
            "type": "command",
            "command_options": [
                {"type": 1, "name": "something", "value": "ignore"},  # type 1 is not string
                {"type": 3, "name": "question", "value": "extract me"},
            ],
        }
        message = extract_message_from_event(event)

        assert message == "extract me"

    def test_returns_none_for_non_command(self):
        """Non-command events return None."""
        event = {"type": "ping"}
        message = extract_message_from_event(event)

        assert message is None

    def test_returns_none_for_no_options(self):
        """Command with no options returns None."""
        event = {
            "type": "command",
            "command_options": [],
        }
        message = extract_message_from_event(event)

        assert message is None


class TestHandlesBodyAsString:
    """Test handling of body as string vs dict."""

    def test_parses_body_as_string(self):
        """Body can be JSON string."""
        event = {
            "headers": {},
            "body": '{"type": 1}',  # String
        }
        parsed = parse_discord_event(event)

        assert parsed["type"] == "ping"

    def test_handles_missing_body(self):
        """Missing body should not crash."""
        event = {"headers": {}}
        parsed = parse_discord_event(event)

        # Should return unknown type
        assert parsed["type"] == "unknown"
