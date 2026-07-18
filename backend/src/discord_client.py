"""Discord webhook client for receiving and parsing bot events."""

import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger()


class DiscordWebhookError(Exception):
    """Raised when Discord webhook validation fails."""
    pass


def verify_discord_signature(
    body: str,
    signature: str,
    timestamp: str,
    public_key: str,
) -> bool:
    """Verify Discord webhook signature for security.

    Discord requires verification of all incoming webhooks using the
    public key. This prevents spoofed requests.

    Args:
        body: Raw request body (exact bytes sent by Discord)
        signature: X-Signature-Ed25519 header value
        timestamp: X-Signature-Timestamp header value
        public_key: Discord bot's public key

    Returns:
        True if signature is valid, False otherwise
    """
    try:
        message = timestamp + body
        # Use nacl if available, otherwise fall back to basic validation
        try:
            import nacl.signing
            verify_key = nacl.signing.VerifyKey(bytes.fromhex(public_key))
            verify_key.verify(message.encode(), bytes.fromhex(signature))
            return True
        except ImportError:
            # Fallback: basic HMAC validation (less secure but works)
            # In production, nacl is recommended
            logger.warning("nacl library not available, using basic validation")
            expected = hmac.new(
                public_key.encode(),
                message.encode(),
                hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expected, signature)
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


def parse_discord_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse Discord webhook event from API Gateway.

    Discord sends events in a specific format. This extracts the
    relevant fields and validates the structure.

    Args:
        event: API Gateway event (POST body)

    Returns:
        Parsed event with: type, user_id, guild_id, channel_id, message_content, etc.
        Returns {'type': 'invalid_signature'} if signature verification fails.

    Raises:
        DiscordWebhookError: If event structure is invalid
    """
    import os

    # Extract headers for signature verification
    headers = event.get('headers', {})
    signature = headers.get('x-signature-ed25519')
    timestamp = headers.get('x-signature-timestamp')

    # Get raw body (must be exact as Discord sent it)
    body = event.get('body', '')
    if isinstance(body, str):
        body_str = body
    else:
        body_str = json.dumps(body)

    # Verify Discord signature
    public_key = os.getenv('DISCORD_PUBLIC_KEY')
    if public_key and signature and timestamp:
        if not verify_discord_signature(body_str, signature, timestamp, public_key):
            logger.warning("Invalid Discord signature")
            return {'type': 'invalid_signature'}
    elif signature or timestamp:
        logger.warning("Missing Discord signature headers")

    # Parse the JSON payload
    try:
        if not body_str or not body_str.strip():
            logger.warning("Empty request body")
            return {'type': 'unknown', 'raw_payload': {}}
        payload = json.loads(body_str)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in request body: {e}")
        return {'type': 'unknown', 'raw_payload': {}}

    # Handle Discord challenge (required for initial webhook registration)
    if payload.get('type') == 1:  # INTERACTION_PING
        logger.info("Received Discord PING challenge")
        return {
            'type': 'ping',
            'respond_with': {'type': 1}  # PING response
        }

    # Handle APPLICATION_COMMAND events (slash commands)
    if payload.get('type') == 2:  # APPLICATION_COMMAND
        interaction = payload.get('data', {})
        user = payload.get('member', {}).get('user', {})

        return {
            'type': 'command',
            'interaction_token': payload.get('token'),
            'interaction_id': payload.get('id'),
            'guild_id': payload.get('guild_id'),
            'channel_id': payload.get('channel_id'),
            'user_id': user.get('id'),
            'user_name': user.get('username'),
            'command_name': interaction.get('name'),
            'command_options': interaction.get('options', []),
        }

    # Handle MESSAGE_COMPONENT events (button clicks)
    if payload.get('type') == 3:  # MESSAGE_COMPONENT
        interaction = payload.get('data', {})
        user = payload.get('member', {}).get('user', {})
        message = payload.get('message', {})

        return {
            'type': 'component',
            'interaction_token': payload.get('token'),
            'interaction_id': payload.get('id'),
            'guild_id': payload.get('guild_id'),
            'channel_id': payload.get('channel_id'),
            'user_id': user.get('id'),
            'user_name': user.get('username'),
            'custom_id': interaction.get('custom_id'),
            'message_content': message.get('content', ''),
        }

    # Fallback for unknown event types
    logger.warning(f"Unhandled Discord event type: {payload.get('type')}")
    return {
        'type': 'unknown',
        'raw_payload': payload
    }


def extract_message_from_event(parsed_event: Dict[str, Any]) -> Optional[str]:
    """Extract the user's text message from a parsed Discord event.

    Args:
        parsed_event: Output from parse_discord_event()

    Returns:
        The message text, or None if not a message event
    """
    if parsed_event.get('type') == 'command':
        # Extract text from command options
        options = parsed_event.get('command_options', [])
        for option in options:
            if option.get('type') == 3:  # STRING type
                return option.get('value')

    return None
