"""Pytest configuration and shared fixtures."""

import json
import pytest
import requests
from typing import Dict, Any


@pytest.fixture
def lambda_url() -> str:
    """URL to running Lambda container (localhost:9000)."""
    return "http://localhost:9000/2015-03-31/functions/function/invocations"


@pytest.fixture
def discord_ping_event() -> Dict[str, Any]:
    """Discord INTERACTION_PING challenge (type=1)."""
    return {
        "headers": {},
        "body": json.dumps({
            "type": 1,  # INTERACTION_PING
        }),
    }


@pytest.fixture
def discord_command_event(monkeypatch) -> Dict[str, Any]:
    """Discord slash command event (type=3, APPLICATION_COMMAND)."""
    return {
        "headers": {},
        "body": json.dumps({
            "type": 3,  # APPLICATION_COMMAND
            "id": "interaction_id_123",
            "token": "interaction_token_abc",
            "guild_id": "1482164431528923170",
            "channel_id": "1487590796436705521",
            "member": {
                "user": {
                    "id": "user_123",
                    "username": "testuser",
                }
            },
            "data": {
                "name": "ask",
                "type": 1,  # CHAT_INPUT
                "options": [
                    {
                        "type": 3,  # STRING
                        "name": "question",
                        "value": "Tell me about Scuz Patrol",
                    }
                ],
            },
        }),
    }


@pytest.fixture
def discord_lore_event() -> Dict[str, Any]:
    """Discord slash command for new lore."""
    return {
        "headers": {},
        "body": json.dumps({
            "type": 3,  # APPLICATION_COMMAND
            "id": "interaction_id_456",
            "token": "interaction_token_def",
            "guild_id": "1482164431528923170",
            "channel_id": "1487590796436705521",
            "member": {
                "user": {
                    "id": "user_456",
                    "username": "lorewriter",
                }
            },
            "data": {
                "name": "add_lore",
                "type": 1,
                "options": [
                    {
                        "type": 3,
                        "name": "lore",
                        "value": "The band was formed in the year 2020",
                    }
                ],
            },
        }),
    }


@pytest.fixture
def lambda_client(lambda_url):
    """HTTP client for hitting Lambda."""
    class LambdaClient:
        def __init__(self, url):
            self.url = url
            self.last_response = None

        def invoke(self, event: Dict[str, Any]) -> Dict[str, Any]:
            """Invoke Lambda with event, return parsed response."""
            try:
                response = requests.post(
                    self.url,
                    json=event,
                    timeout=10,
                )
                response.raise_for_status()
                self.last_response = response
                return response.json()
            except requests.exceptions.ConnectionError:
                raise RuntimeError(
                    "Lambda container not running. Start with: task start:backend"
                )
            except Exception as e:
                raise RuntimeError(f"Lambda invocation failed: {e}")

        def get_body(self, response: Dict[str, Any]) -> Dict[str, Any]:
            """Parse JSON body from Lambda response."""
            body = response.get("body", "{}")
            if isinstance(body, str):
                return json.loads(body)
            return body

    return LambdaClient(lambda_url)
