# Scuz Patrol Discord Bot

Interactive Discord bot for the Scuz Patrol fictional band worldbuilding project.

See [CLAUDE.md](CLAUDE.md) for the full vision, architecture, and setup details.

## Quick start

```bash
# Initialize Python venv and install dependencies
task init:backend

# Run tests to verify everything works
task test:unit

# Build Docker image
task build:backend

# Plan infrastructure
task build:infra:dev

# Deploy
task deploy:infra:dev
task deploy:backend:dev
```

## Testing

**Always run tests before declaring work complete:**

```bash
task test:unit            # Fast unit tests (no external services)
task test:integration     # Integration tests (requires running container)
task test:all             # Full suite with coverage
task format               # Auto-format code
task lint                 # Check types and style
```

See `backend/TESTING.md` for details.

## Commands

See `Taskfile.yml` for all available tasks.
