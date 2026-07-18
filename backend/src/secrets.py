"""Secrets management with Secrets Manager fallback to env vars."""

import json
import logging
import os
import boto3
from typing import Optional

logger = logging.getLogger()


def get_secret(secret_name: str, env_var_name: Optional[str] = None) -> str:
    """Fetch secret from AWS Secrets Manager or environment variable.

    In AWS Lambda, reads from Secrets Manager. Locally, falls back to env vars
    for testing without AWS credentials.

    Args:
        secret_name: Name/ARN of secret in Secrets Manager
        env_var_name: Environment variable name for local fallback

    Returns:
        Secret value as string

    Raises:
        ValueError: If secret not found in either location
    """
    # Try environment variable first (for local testing)
    env_var = env_var_name or secret_name.upper().replace('-', '_')
    if env_value := os.getenv(env_var):
        logger.debug(f"Using secret from environment variable: {env_var}")
        return env_value

    # Try AWS Secrets Manager (in Lambda)
    try:
        logger.debug(f"Fetching secret from Secrets Manager: {secret_name}")
        client = boto3.client('secretsmanager')
        response = client.get_secret_value(SecretId=secret_name)
        return response['SecretString']
    except Exception as e:
        logger.warning(f"Failed to fetch from Secrets Manager: {e}")
        raise ValueError(
            f"Secret '{secret_name}' not found in Secrets Manager or env var '{env_var}'"
        )


def get_discord_token() -> str:
    """Get Discord bot token."""
    return get_secret('scuz-patrol-bot-dev/discord-token', 'DISCORD_BOT_TOKEN')


def get_anthropic_api_key() -> str:
    """Get Anthropic API key."""
    return get_secret('scuz-patrol-bot-dev/anthropic-api-key', 'ANTHROPIC_API_KEY')


def get_google_service_account_key() -> str:
    """Get Google service account key (JSON string)."""
    return get_secret(
        'scuz-patrol-bot-dev/google-service-account',
        'GOOGLE_SERVICE_ACCOUNT_KEY'
    )
