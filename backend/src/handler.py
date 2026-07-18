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
from claude_client import ClaudeClient
from google_docs_client import GoogleDocsClient

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
            "body": json.dumps(response_body),
        }

    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
        }
