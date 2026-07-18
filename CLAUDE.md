# Scuz Patrol Discord Bot

Interactive Discord bot for the Scuz Patrol fictional band worldbuilding project. Listens for messages in Discord, classifies them as lore/questions, and either adds new canon to the Google Doc or answers questions using Claude AI with prompt caching for cost optimization.

## Vision

The bot runs as a Lambda function triggered by Discord webhook events. It:
1. **Listens for mentions** in the Scuz Patrol Discord server
2. **Classifies input** as: new lore, lore question, or neither (via Claude API)
3. **For new lore**: suggests where it goes, waits for user confirmation, then writes to the canon Google Doc
4. **For questions**: reads the canon doc, answers with citations
5. **Uses prompt caching** to cache the full canon doc on Anthropic's side, reducing token costs for repeated questions

## Architecture

```
Discord message → Lambda (via API Gateway webhook)
                → Claude API (classifies + answers)
                → Google Docs API (reads/writes canon)
                → Response back to Discord
```

**Key constraints:**
- Lambda is stateless; no persistent local storage between invocations
- Prompt cache lives on Anthropic's servers, tied to API account
- Cache is busted when canon doc changes (different byte hash)
- Cache expires automatically after 5 minutes

## Setup

### Prerequisites
- AWS account with Lambda, API Gateway, IAM
- Anthropic API key (for Claude)
- Discord bot token (already created: `ScuzPatrolHistoryBot`)
- Google Docs service account key (already created)
- Terraform

### Environment variables (.env, never commit)
```
DISCORD_BOT_TOKEN=<bot-token>
ANTHROPIC_API_KEY=<api-key>
GOOGLE_SERVICE_ACCOUNT_KEY=<json-key>
GOOGLE_DOC_ID=1gJuZ9CBbNz5vQ1xDEDDQRZLI5TyBFGGa4YGvWp1gwgE
DISCORD_GUILD_ID=1482164431528923170
```

## Repo structure

```
scuz-patrol-bot/
├── backend/
│   ├── src/
│   │   ├── __init__.py
│   │   ├── handler.py           # Lambda entry point
│   │   ├── discord_client.py    # Discord event parsing
│   │   ├── claude_client.py     # Claude API integration
│   │   └── google_docs_client.py # Google Docs API integration
│   ├── requirements.txt
│   └── Dockerfile (if using container)
├── terraform/
│   ├── service/                 # Reusable modules
│   │   ├── lambda.tf
│   │   ├── api_gateway.tf
│   │   ├── iam.tf
│   │   └── variables.tf
│   └── envs/
│       └── dev/                 # Dev environment
│           ├── main.tf
│           ├── variables.tf
│           ├── outputs.tf
│           └── versions.tf
├── Taskfile.yml                 # Build/deploy tasks
├── .gitignore
└── CLAUDE.md (this file)
```

## Golden Rule: Always Check Tasks First

**Before running any manual command or operation, check `Taskfile.yml` for an existing task.**

Tasks are the source of truth for how work gets done:
- Build, test, deploy, format, lint — all have `task` commands
- Task dependencies ensure correct order (e.g., `test:integration` depends on `build:backend`)
- Using tasks keeps workflow consistent and reproducible

If you need to do something and there's no task, create one in `Taskfile.yml` and document it in CLAUDE.md.

**When adding new tasks: read existing tasks first.** Don't invent new patterns. If other tasks source `.env` one way, all tasks should do it the same way. Check what's already there before coding.

## Development workflow

## Testing & Verification

**Always run tests before declaring work complete.** Do not make changes and claim they work without verifying:

```bash
# Unit tests (fast, mocked, no services needed)
task test:unit

# Integration tests (rebuilds/restarts container, tests real Lambda)
# Use this to verify changes work end-to-end
task test:integration

# Full test suite with coverage reporting
task test:coverage
task test:all

# Format and lint:
task format
task lint
```

**Local development workflow:**
1. Make code changes in `backend/src/`
2. Run `task test:unit` to verify logic (fast feedback)
3. Run `task test:integration` when ready to test against running Lambda container
4. The integration test task handles: building image, restarting container, running pytest

If a test fails, fix it. Don't move forward with broken tests. See `backend/TESTING.md` for details.

## Deployment

Infrastructure is deployed to AWS (dev environment) via Terraform.

**CRITICAL: Secrets Management**

⚠️ **I (the assistant) will never touch `.env` or run any secret-related tasks.** You must do this yourself.

Your `.env` file must define these credentials (never commit):
```
DISCORD_BOT_TOKEN=...              # Bot token from Discord Developer Portal
DISCORD_PUBLIC_KEY=...             # Public key for signature verification
DISCORD_APPLICATION_ID=...         # Application ID
ANTHROPIC_API_KEY=...              # Claude API key
GOOGLE_SERVICE_ACCOUNT_KEY=...     # GCP service account JSON
GUILD_ID=...                       # Discord server ID
```

**To deploy with secrets**:
1. **You populate `.env`** with the values above
2. **You run** `task set-secrets:dev` to push to AWS Secrets Manager
3. **You run** `task register:commands:dev` to register slash commands with Discord

I will never run these tasks or read `.env` — you handle all credential management.

## Known decisions

- **Docker container deployment** with ECR — enables local testing via `docker run` before AWS deployment
- **Secrets at runtime** — Lambda handler fetches from Secrets Manager at startup, falls back to env vars for local testing
- **No persistent state** between invocations — each Discord message is independent
- **Prompt cache strategy**: fetch canon doc from Google Docs on every invocation, let Anthropic's cache handle deduplication
- **Single-file tests**: unit tests mock everything; integration tests use real running Lambda
- **Webhook-based architecture**: simpler than persistent connections, scales with Discord's delivery model

## Local development tasks

```bash
task build:backend           # Build Docker image
task start:backend           # Start/restart container at localhost:9000
task test:unit              # Run mocked unit tests (fast)
task test:integration       # Rebuild, restart container, run integration tests
task test:coverage          # Generate coverage report with branch analysis
task format                  # Auto-format code (black, ruff)
task lint                    # Check code quality (ruff, mypy)
```
