"""Lambda handler for Scuz Patrol Discord bot.

Receives Discord webhook events, classifies them with Claude,
and either answers lore questions or writes to the canon Google Doc.
"""

import json
import os
import logging
from typing import Any, Dict
from dotenv import load_dotenv

from discord_client import parse_discord_event, extract_message_from_event

# Load .env for local testing (no-op in Lambda)
load_dotenv()

logger = logging.getLogger()
logger.setLevel(os.getenv('LOG_LEVEL', 'INFO'))


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

        # Handle ping challenges from Discord
        if parsed_event.get('type') == 'ping':
            return {
                "statusCode": 200,
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

        # TODO: Call Claude to classify intent
        # TODO: Handle response (answer question or write lore)
        # TODO: Post response back to Discord

        return {
            "statusCode": 200,
            "body": json.dumps({"message": "received"}),
        }
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
