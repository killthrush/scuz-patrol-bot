"""Lambda handler for Scuz Patrol Discord bot.

Receives Discord webhook events, classifies them with Claude,
and either answers lore questions or writes to the canon Google Doc.
"""

import json
import os
import logging
import boto3
from typing import Any, Dict
from dotenv import load_dotenv

from src.discord_client import parse_discord_event, extract_message_from_event
from src.claude_client import ClaudeClient
from src.google_docs_client import GoogleDocsClient

# Load .env for local testing (no-op in Lambda)
load_dotenv()

logger = logging.getLogger()
logger.setLevel(os.getenv('LOG_LEVEL', 'INFO'))


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


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda entry point.

    Args:
        event: API Gateway event (contains Discord webhook POST body)
        context: Lambda context

    Returns:
        HTTP response with status code and body
    """
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
                "body": json.dumps({"error": "No message found"}),
            }

        logger.info(f"User message: {message}")

        # Initialize clients
        try:
            claude = ClaudeClient()
            docs = GoogleDocsClient()
        except ValueError as e:
            logger.error(f"Failed to initialize clients: {e}")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Service initialization failed"}),
            }

        # Fetch the current canon doc
        try:
            canon_doc = docs.read_document()
        except Exception as e:
            logger.error(f"Failed to read canon doc: {e}")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Failed to fetch canon"}),
            }

        # Classify the user's message intent
        try:
            classification = claude.classify_intent(message, canon_doc)
        except Exception as e:
            logger.error(f"Failed to classify intent: {e}")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "Classification failed"}),
            }

        logger.info(f"Classification: {classification}")
        intent = classification.get('intent', 'neither')

        # Handle based on intent
        if intent == 'question':
            # Answer the lore question
            try:
                answer = claude.answer_question(message, canon_doc)
                logger.info(f"Generated answer: {answer[:200]}...")
                response_body = {
                    "intent": "answer",
                    "answer": answer,
                }
            except Exception as e:
                logger.error(f"Failed to generate answer: {e}")
                response_body = {"error": "Failed to generate answer"}

        elif intent == 'new_lore':
            # Acknowledge the lore and suggest section
            section = classification.get('suggested_section', 'Unexplored Ideas')
            message = f"Interesting! I think this belongs in **{section}**. React with ✓ to add it, or ✗ to discard."
            response_body = {
                "intent": "new_lore",
                "message": message,
                "suggested_section": section,
            }

        else:  # neither
            response_body = {
                "intent": "neither",
                "message": "I didn't recognize that as Scuz lore. Ask me about the band, characters, or songs!",
            }

        logger.info(f"Response: {json.dumps(response_body)}")

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(response_body),
        }

    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
        }
