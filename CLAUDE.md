# Scuz Patrol Discord Bot

Interactive Discord bot for the Scuz Patrol fictional band worldbuilding project. Listens for messages in Discord, classifies them as lore/questions, and either records new canon as a fact or answers questions directly from the fact store using Claude AI.

## Vision

The bot runs as a Lambda function triggered by Discord webhook events, backed by a durable fact store that is the sole source of truth — there is no separate canon document. It:
1. **Listens for mentions and slash commands** (`/lore`, `/ask`, `/refresh-songs`) in the Scuz Patrol Discord server
2. **Classifies input** as: new lore, lore question, or neither (via Claude API)
3. **For new lore**: suggests where it goes, waits for user confirmation, and records it as a fact in DynamoDB
4. **For Suno song activity**: an SQS-driven worker mines song captions/lyrics/comments for lore, lyrics, and credit facts and records them in DynamoDB
5. **For questions**: reads all non-superseded lore facts from DynamoDB directly, answers with citations
6. **Uses prompt caching** on Anthropic's side to reduce token costs for repeated questions

An earlier design regenerated a canon Google Doc from the fact store on every write (EventBridge-debounced reconstruction Lambda). That doc/pipeline was never wired up / has since been removed — DynamoDB is read directly, with no separate document to keep in sync.

## Architecture

```
Discord message/command → Lambda (via API Gateway webhook)
                         → Claude API (classifies + answers directly from facts)
                         → DynamoDB fact store (records/reads facts)
                         → Response back to Discord

Suno song event → SQS queue → Lambda (mines lore/lyrics/credit facts)
                             → DynamoDB fact store
                             → S3 manifest bucket (per-song tracking artifact, dedupes mined comments)
```

**Key constraints:**
- Lambda is stateless; no persistent local storage between invocations
- Prompt cache lives on Anthropic's servers, tied to API account
- Cache expires automatically after 5 minutes
- Facts in DynamoDB are append-only — never delete a fact; transition its `status` instead (`pending` → `integrated`, or `superseded` for retcons). This applies to test-state resets too.

## Setup

### Prerequisites
- AWS account with Lambda, API Gateway, IAM
- Anthropic API key (for Claude)
- Discord bot token (already created: `ScuzPatrolHistoryBot`)
- Terraform

### Environment variables (.env, never commit)
```
DISCORD_BOT_TOKEN=<bot-token>
ANTHROPIC_API_KEY=<api-key>
DISCORD_GUILD_ID=1482164431528923170
```

`GOOGLE_SERVICE_ACCOUNT_KEY` / `GOOGLE_DOC_ID` and the `google-api-python-client` dependency are leftover from the retired canon-doc design (see Vision above) — unused by current code. `task set-secrets:dev` no longer pushes the Google key (removed, since Terraform never provisioned a secret for it); `.env.example` and `requirements.txt` still list the leftovers and can be trimmed whenever someone's next in there.

## Repo structure

```
scuz-patrol-bot/
├── backend/
│   ├── src/
│   │   ├── __init__.py
│   │   ├── handler.py             # Lambda entry point
│   │   ├── discord_client.py      # Discord event parsing
│   │   ├── claude_client.py       # Claude API integration
│   │   ├── fact_store.py          # DynamoDB-backed fact store (source of truth)
│   │   ├── suno_client.py         # Suno API mining + S3 manifest tracking
│   │   └── calibration_queries.py # Classifier calibration queries
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
- **Prompt cache strategy**: fetch all lore facts from DynamoDB on every invocation, let Anthropic's cache handle deduplication
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
