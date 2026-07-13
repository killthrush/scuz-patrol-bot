# Scuz Patrol Discord Bot

Interactive Discord bot for the Scuz Patrol fictional band worldbuilding project.

See [CLAUDE.md](CLAUDE.md) for the full vision, architecture, and setup details.

## Quick start

```bash
# Install dependencies
task install-deps:backend

# Build Lambda deployment package
task build:backend

# Plan infrastructure
task build:infra:dev

# Deploy
task deploy:infra:dev
task deploy:backend:dev
```

## Commands

See `Taskfile.yml` for all available tasks.
