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

## Development workflow (one step at a time)

1. **Backend handler stub** — basic Lambda entry point that receives Discord events
2. **Discord client** — parse webhook events, extract user message + metadata
3. **Claude integration** — call Claude to classify intent + generate response
4. **Google Docs integration** — read current canon, write new entries
5. **Terraform** — Lambda + API Gateway + IAM roles
6. **Deploy & test** — local testing, then deploy to dev environment

## Known decisions

- **Python zip deployment** (not Docker) for simplicity and faster cold starts
- **No persistent state** between invocations — each message is independent
- **Prompt cache strategy**: fetch canon doc from Google Docs on every invocation, let Anthropic's cache handle the rest
- **Two environments**: `dev` for testing, live deployment later
- **Webhook-based, not gateway**: simpler for a single-purpose bot, no persistent connection needed

## Next steps

See Taskfile.yml for available tasks. Start with:
```bash
task install-deps:backend
task build:backend
task build:infra:dev
```

Then move to implementing the handler stub.
