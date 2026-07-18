"""Lambda handler for Scuz Patrol Discord bot.

Receives Discord webhook events, classifies them with Claude,
and either answers lore questions or writes to the canon Google Doc.

Discord requires an interaction response within 3 seconds. Since
classification + Google Docs + Claude calls can exceed that, slash
commands are acknowledged immediately with a deferred response (type 5),
then processed by asynchronously self-invoking this same Lambda. The
async invocation does the real work and posts the answer via Discord's
follow-up webhook.
"""

import json
import os
import logging
import boto3
import requests  # type: ignore
from typing import Any, Dict, Optional
from dotenv import load_dotenv

from src.discord_client import parse_discord_event, extract_message_from_event
from src.claude_client import ClaudeClient
from src.google_docs_client import GoogleDocsClient

# Load .env for local testing (no-op in Lambda)
load_dotenv()

logger = logging.getLogger()
logger.setLevel(os.getenv('LOG_LEVEL', 'INFO'))

DISCORD_API_BASE = "https://discord.com/api/v10"


def _initialize_secrets() -> None:
    """Fetch secrets from Secrets Manager and set as env vars.

    Runs once at Lambda startup. If env vars already set (local testing),
    skips Secrets Manager fetch. This ensures all code paths use env vars.
    Gracefully handles local testing where Secrets Manager is unavailable.
    """
    secrets = {
        'ANTHROPIC_API_KEY': 'scuz-patrol-bot-dev/anthropic-api-key',
        'DISCORD_BOT_TOKEN': 'scuz-patrol-bot-dev/discord-token',
        'GOOGLE_SERVICE_ACCOUNT_KEY': 'scuz-patrol-bot-dev/google-service-account',
        'DISCORD_PUBLIC_KEY': 'scuz-patrol-bot-dev/discord-public-key',
        'DISCORD_APPLICATION_ID': 'scuz-patrol-bot-dev/discord-application-id',
    }

    for env_var, secret_name in secrets.items():
        # Skip if already set (local testing with .env)
        if os.getenv(env_var):
            continue

        try:
            client = boto3.client('secretsmanager', region_name='us-east-1')
            response = client.get_secret_value(SecretId=secret_name)
            os.environ[env_var] = response['SecretString']
            logger.debug(f"Loaded {env_var} from Secrets Manager")
        except Exception as e:
            # Gracefully skip if Secrets Manager is unavailable (local testing)
            logger.debug(
                f"Secrets Manager unavailable for {env_var} (expected in local testing): {e}"
            )


# Initialize secrets once at Lambda startup
_initialize_secrets()


def _process_message(message: str) -> Dict[str, Any]:
    """Classify a user message and build the response payload.

    Returns a dict with either an "error" key, or "intent" + "content"
    (plus intent-specific fields like "suggested_section").
    """
    try:
        claude = ClaudeClient()
        docs = GoogleDocsClient()
    except ValueError as e:
        logger.error(f"Failed to initialize clients: {e}")
        return {"error": "Service initialization failed"}

    try:
        canon_doc = docs.read_document()
    except Exception as e:
        logger.error(f"Failed to read canon doc: {e}")
        return {"error": "Failed to fetch canon"}

    try:
        classification = claude.classify_intent(message, canon_doc)
    except Exception as e:
        logger.error(f"Failed to classify intent: {e}")
        return {"error": "Classification failed"}

    logger.info(f"Classification: {classification}")
    intent = classification.get('intent', 'neither')

    if intent == 'question':
        try:
            answer = claude.answer_question(message, canon_doc)
            logger.info(f"Generated answer: {answer[:200]}...")
            return {"intent": "answer", "content": answer}
        except Exception as e:
            logger.error(f"Failed to generate answer: {e}")
            return {"error": "Failed to generate answer"}

    if intent == 'new_lore':
        section = classification.get('suggested_section', 'Unexplored Ideas')
        content = (
            f"Interesting! I think this belongs in **{section}**. "
            f"React with ✓ to add it, or ✗ to discard."
        )
        return {"intent": "new_lore", "content": content, "suggested_section": section}

    return {
        "intent": "neither",
        "content": "I didn't recognize that as Scuz lore. Ask me about the band, characters, or songs!",
    }


def _send_discord_followup(interaction_token: str, content: str) -> None:
    """Send the real answer to Discord via the interaction follow-up webhook."""
    application_id = os.getenv('DISCORD_APPLICATION_ID')
    url = f"{DISCORD_API_BASE}/webhooks/{application_id}/{interaction_token}/messages/@original"
    try:
        response = requests.patch(url, json={"content": content}, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Discord follow-up: {e}")


def _handle_async_worker(event: Dict[str, Any]) -> Dict[str, Any]:
    """Do the real work for a deferred command, then notify Discord."""
    message: Optional[str] = event.get('message')
    interaction_token: Optional[str] = event.get('interaction_token')

    if not message or not interaction_token:
        logger.error("Async worker invoked without message or interaction_token")
        return {"statusCode": 400}

    result = _process_message(message)
    reply: str = str(result.get('content') or result.get('error', 'Something went wrong.'))
    content = f"> **{message}**\n\n{reply}"
    _send_discord_followup(interaction_token, content)
    return {"statusCode": 200}


def _defer_and_process_async(parsed_event: Dict[str, Any]) -> Dict[str, Any]:
    """Acknowledge the interaction immediately, do the real work asynchronously."""
    function_name = os.getenv('AWS_LAMBDA_FUNCTION_NAME')

    if function_name:
        try:
            lambda_client = boto3.client('lambda')
            lambda_client.invoke(
                FunctionName=function_name,
                InvocationType='Event',
                Payload=json.dumps({
                    'source': 'discord_async_worker',
                    'message': parsed_event.get('message'),
                    'interaction_token': parsed_event.get('interaction_token'),
                }).encode('utf-8'),
            )
        except Exception as e:
            logger.error(f"Failed to invoke async worker: {e}")
    else:
        logger.warning(
            "AWS_LAMBDA_FUNCTION_NAME not set; skipping async worker invocation (local testing?)"
        )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"type": 5}),  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda entry point.

    Args:
        event: API Gateway event (contains Discord webhook POST body), or an
            async self-invocation payload (source == 'discord_async_worker')
        context: Lambda context

    Returns:
        HTTP response with status code and body
    """
    # This Lambda self-invokes asynchronously to do slow work outside
    # Discord's 3-second interaction window; recognize those invocations.
    if event.get('source') == 'discord_async_worker':
        return _handle_async_worker(event)

    try:
        logger.info(f"Received event: {json.dumps(event)}")

        # Parse the Discord webhook event
        parsed_event = parse_discord_event(event)
        logger.info(f"Parsed event type: {parsed_event.get('type')}")

        # Reject requests with invalid signatures
        if parsed_event.get('type') == 'invalid_signature':
            return {
                "statusCode": 401,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Invalid signature"}),
            }

        # Handle ping challenges from Discord
        if parsed_event.get('type') == 'ping':
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(parsed_event.get('respond_with', {})),
            }

        # Extract the user's message
        message = extract_message_from_event(parsed_event)
        if not message:
            logger.warning("No message extracted from event")
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "No message found"}),
            }

        logger.info(f"User message: {message}")
        parsed_event['message'] = message
        return _defer_and_process_async(parsed_event)

    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
        }
