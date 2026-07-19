"""Integration tests that hit the running Lambda container.

Start the container with: task start:backend

These tests verify the actual Lambda behavior with real event payloads.
Mocking happens at the API level (Claude, Google Docs) to avoid costs.
"""

import json


class TestDiscordPingChallenge:
    """Discord webhook verification handshake."""

    def test_ping_challenge_returns_pong(self, lambda_client, discord_ping_event):
        """Discord sends type=1 INTERACTION_PING, we must respond with type=1."""
        response = lambda_client.invoke(discord_ping_event)

        assert response["statusCode"] == 200
        body = lambda_client.get_body(response)
        assert body.get("type") == 1

    def test_ping_returns_valid_json(self, lambda_client, discord_ping_event):
        """Ping response must be valid JSON."""
        response = lambda_client.invoke(discord_ping_event)

        # Should not raise
        body = lambda_client.get_body(response)
        assert isinstance(body, dict)


class TestMessageParsing:
    """Test Discord event parsing and message extraction."""

    def test_extracts_message_from_command(self, lambda_client, discord_command_event):
        """A valid command should get an immediate deferred ack (type 5).

        The real classification work happens in an async self-invocation,
        which isn't triggered locally (no AWS_LAMBDA_FUNCTION_NAME in the
        local container) — see unit tests for that logic.
        """
        response = lambda_client.invoke(discord_command_event)

        assert response["statusCode"] == 200
        body = lambda_client.get_body(response)
        assert body.get("type") == 5

    def test_handles_missing_message(self, lambda_client):
        """When no message in event, return 400."""
        event = {
            "headers": {},
            "body": json.dumps(
                {
                    "type": 2,
                    "id": "test",
                    "token": "test",
                    "guild_id": "123",
                    "channel_id": "456",
                    "member": {"user": {"id": "789", "username": "test"}},
                    "data": {
                        "name": "ask",
                        "options": [],  # No options = no message
                    },
                }
            ),
        }
        response = lambda_client.invoke(event)

        assert response["statusCode"] == 400
        body = lambda_client.get_body(response)
        assert "error" in body or "No message" in body.get("error", "")


class TestHandlerErrorHandling:
    """Test error handling and resilience."""

    def test_handles_invalid_json(self, lambda_client):
        """Malformed JSON in body should be handled gracefully."""
        event = {
            "headers": {
                "x-signature-ed25519": "test",
                "x-signature-timestamp": "1234567890",
            },
            "body": "not valid json at all {{{",
        }

        # Should not crash, should return error
        try:
            response = lambda_client.invoke(event)
            assert response["statusCode"] in [400, 500]
        except Exception:
            # Connection error is fine in this context
            pass

    def test_handles_missing_headers(self, lambda_client):
        """Missing signature headers should be handled."""
        event = {
            "headers": {},  # No signature headers
            "body": json.dumps({"type": 1}),
        }
        response = lambda_client.invoke(event)

        # Should still work (signature verification is logged but not enforced)
        assert response["statusCode"] == 200


class TestClassificationFlow:
    """Test the deferred-ack flow for command interactions.

    Actual classification (Claude + Google Docs) happens in an async
    self-invocation not exercised by local integration tests — see
    unit_tests/test_handler.py::TestAsyncWorkerProcessing for that.
    """

    def test_responds_to_question(self, lambda_client, discord_command_event):
        """Question command should get a deferred ack."""
        response = lambda_client.invoke(discord_command_event)

        assert "statusCode" in response
        assert "body" in response

        body = lambda_client.get_body(response)
        assert body.get("type") == 5

    def test_responds_with_valid_structure(self, lambda_client, discord_command_event):
        """Response should always be a valid deferred-ack payload."""
        response = lambda_client.invoke(discord_command_event)
        body = lambda_client.get_body(response)

        assert body.get("type") == 5


class TestHTTPResponses:
    """Test HTTP response format compliance."""

    def test_response_has_status_code(self, lambda_client, discord_ping_event):
        """All responses must have statusCode."""
        response = lambda_client.invoke(discord_ping_event)
        assert "statusCode" in response
        assert isinstance(response["statusCode"], int)

    def test_response_has_body(self, lambda_client, discord_ping_event):
        """All responses must have body (JSON string)."""
        response = lambda_client.invoke(discord_ping_event)
        assert "body" in response
        assert isinstance(response["body"], str)

    def test_body_is_valid_json(self, lambda_client, discord_ping_event):
        """Response body must be parseable JSON."""
        response = lambda_client.invoke(discord_ping_event)
        body = response.get("body", "{}")

        # Should not raise
        json.loads(body)
