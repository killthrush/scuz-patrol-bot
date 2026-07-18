# Testing Guide for Scuz Patrol Bot

## Important: Always Verify with Tests

**Do not declare tasks complete without running the actual tests.** This project has comprehensive tests — use them.

When making changes:
1. Run `task test:unit` to verify your code works
2. If it fails, fix the issue (don't just claim it should work)
3. Run `task test:all` for coverage report
4. Only after tests pass is the work actually done

## Overview

Two-tier testing strategy:

1. **Unit Tests** — Fast, isolated, no external services
2. **Integration Tests** — Real Lambda container, realistic flows

## Python Environment

### Dev Dependencies (requirements-dev.txt)

```
pytest==7.4.0           # Test runner
pytest-cov==4.1.0       # Coverage reporting
pytest-asyncio==0.21.1  # Async test support
pytest-mock==3.11.1     # Mocking utilities
black==23.7.0           # Code formatter
ruff==0.0.278           # Linter
mypy==1.4.1             # Type checker
```

### Setup

```bash
# Initialize venv and install dev dependencies
task init:backend

# This creates a local venv/ directory and installs all dependencies into it.
# All tasks automatically use this venv.
```

## Running Tests

### Unit Tests (No Container Required)

Fast tests that mock external APIs. Safe to run anytime.

```bash
task test:unit
```

Tests:
- `test_discord_client.py` — Discord webhook parsing
- `test_claude_client.py` — Claude API integration (mocked)

### Integration Tests (Container Required)

Real Lambda container with realistic event payloads. Tests HTTP behavior.

```bash
# Terminal 1: Start the container
task start:backend

# Terminal 2: Run integration tests
task test:integration
```

Tests:
- `integration_tests.py` — Full Lambda flow
  - Ping challenge (Discord verification)
  - Message parsing
  - Error handling
  - HTTP response format
  - Intent classification flow

### All Tests with Coverage

```bash
task test:all
```

Generates HTML coverage report at `backend/htmlcov/index.html`

### Watch Mode (Rerun on File Change)

```bash
task test:watch
```

Requires `pytest-watch`:
```bash
pip install pytest-watch
```

## Test Structure

```
backend/
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # Shared fixtures
│   ├── integration_tests.py         # Tests that hit running container
│   └── unit_tests/
│       ├── __init__.py
│       ├── test_discord_client.py   # Discord parsing (unit)
│       └── test_claude_client.py    # Claude API (mocked)
├── pytest.ini                       # Pytest configuration
└── TESTING.md                       # This file
```

## Fixtures (conftest.py)

### lambda_client

HTTP client for invoking Lambda at `localhost:9000`.

```python
def test_something(lambda_client, discord_ping_event):
    response = lambda_client.invoke(discord_ping_event)
    assert response["statusCode"] == 200
```

### Event Fixtures

Pre-built Discord events for testing:

- `discord_ping_event` — INTERACTION_PING (verification challenge)
- `discord_command_event` — Slash command (question)
- `discord_lore_event` — Slash command (new lore)

```python
def test_question(lambda_client, discord_command_event):
    response = lambda_client.invoke(discord_command_event)
    body = lambda_client.get_body(response)
    assert "intent" in body
```

## Integration Test Examples

### Test Ping Challenge

```python
def test_ping_challenge_returns_pong(lambda_client, discord_ping_event):
    response = lambda_client.invoke(discord_ping_event)
    assert response["statusCode"] == 200
    body = lambda_client.get_body(response)
    assert body.get("type") == 1
```

### Test Message Parsing

```python
def test_extracts_message_from_command(lambda_client, discord_command_event):
    response = lambda_client.invoke(discord_command_event)
    body = lambda_client.get_body(response)
    assert "intent" in body or "error" in body
```

### Test Error Handling

```python
def test_handles_invalid_json(lambda_client):
    event = {
        "headers": {"x-signature-ed25519": "test", "x-signature-timestamp": "1234567890"},
        "body": "not valid json {{{",
    }
    response = lambda_client.invoke(event)
    assert response["statusCode"] in [400, 500]
```

## Unit Test Examples

### Mock Claude API

```python
def test_classifies_question(monkeypatch, mock_anthropic):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key")

    mock_response = Mock()
    mock_response.content = [Mock(text='{"intent": "question", ...}')]
    mock_response.usage = Mock(input_tokens=100, output_tokens=50)

    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response
    mock_anthropic.return_value = mock_client

    client = ClaudeClient(api_key="test_key")
    result = client.classify_intent("What is Scuz?", "...")
    assert result["intent"] == "question"
```

## Code Quality Tools

### Format Code

```bash
task format
```

Runs `black` (code formatter) and `ruff` (auto-fixes).

### Lint

```bash
task lint
```

Runs `ruff` (linter) and `mypy` (type checker).

## Workflow

Typical development workflow:

```bash
# 1. Install deps
task install-deps:backend

# 2. Write code + tests
# Edit src/handler.py, tests/unit_tests/test_*.py

# 3. Run unit tests
task test:unit

# 4. Format and lint
task format
task lint

# 5. Build and start container
task build:backend
task start:backend

# 6. Run integration tests (in another terminal)
task test:integration

# 7. Deploy
task build:infra:dev
task deploy:infra:dev
task deploy:backend:dev
```

## Debugging Failed Tests

### Integration Test Fails

If `test:integration` can't connect to Lambda:

```bash
# Check container is running
docker ps | grep scuz-patrol

# If not running, start it
task start:backend

# Check logs
docker logs scuz-patrol
```

### Unit Test Fails

Isolate the test:

```bash
cd backend
pytest tests/unit_tests/test_discord_client.py::TestParseDiscordEvent::test_parses_ping_challenge -v
```

### Check Coverage

```bash
task test:all
open htmlcov/index.html
```

Shows which lines are covered and which aren't.

## Adding New Tests

### Integration Test (Tests Running Container)

```python
# tests/integration_tests.py

class TestMyFeature:
    def test_something(self, lambda_client, discord_command_event):
        response = lambda_client.invoke(discord_command_event)
        assert response["statusCode"] == 200
```

### Unit Test (Mocked APIs)

```python
# tests/unit_tests/test_mymodule.py

def test_something(monkeypatch, mock_anthropic):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    
    mock_response = Mock()
    mock_response.content = [Mock(text='...')]
    mock_client = Mock()
    mock_client.messages.create.return_value = mock_response
    mock_anthropic.return_value = mock_client
    
    # Test your code
```

## Next Steps

1. Run unit tests to verify parsing logic
2. Start container and run integration tests
3. Add tests for new features as you build them
4. Use `pytest -k` to run specific tests
5. Monitor coverage with `task test:all`
