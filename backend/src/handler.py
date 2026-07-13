"""Lambda handler for Scuz Patrol Discord bot.

Receives Discord webhook events, classifies them with Claude,
and either answers lore questions or writes to the canon Google Doc.
"""

import json
import os
import logging
from typing import Any, Dict

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda entry point.

    Args:
        event: Discord webhook event (POST body)
        context: Lambda context

    Returns:
        HTTP response with status code and body
    """
    try:
        logger.info(f"Received event: {json.dumps(event)}")

        # TODO: Parse Discord webhook event
        # TODO: Verify Discord signature
        # TODO: Extract user message and metadata
        # TODO: Call Claude to classify intent
        # TODO: Handle response (answer question or write lore)

        return {
            "statusCode": 200,
            "body": json.dumps({"message": "ok"}),
        }
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
