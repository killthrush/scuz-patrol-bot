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
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

from src.discord_client import parse_discord_event, extract_message_from_event
from src.claude_client import ClaudeClient
from src.google_docs_client import GoogleDocsClient
from src import suno_client

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
        return {"intent": "new_lore", "suggested_section": section}

    return {
        "intent": "neither",
        "content": "I didn't recognize that as Scuz lore. Ask me about the band, characters, or songs!",
    }


def _build_lore_confirmation_message(text: str, section: str) -> Dict[str, Any]:
    """Build a Discord message asking the user to confirm/discard new lore.

    The lore text and section are stored as structured embed fields (not
    interpolated into free text) so a later button click can recover them by
    reading exact field values — no delimiter for a user's own submitted text
    to spoof or corrupt, unlike parsing them back out of a formatted string.
    """
    embed = {
        "title": "🆕 New lore suggestion",
        "description": "Add this to the canon?",
        "fields": [
            {"name": "Section", "value": section},
            {"name": "Lore", "value": text},
        ],
    }
    components = [
        {
            "type": 1,  # ACTION_ROW
            "components": [
                {"type": 2, "style": 3, "label": "Confirm", "custom_id": "lore_confirm"},
                {"type": 2, "style": 4, "label": "Discard", "custom_id": "lore_discard"},
            ],
        }
    ]
    return {"content": "", "embeds": [embed], "components": components}


def _parse_lore_message(embeds: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Recover the lore text and section stored in a confirmation message's embed."""
    if not embeds:
        return None

    fields = {f.get("name"): f.get("value") for f in embeds[0].get("fields", [])}
    section = fields.get("Section")
    text = fields.get("Lore")
    if not section or not text:
        return None
    return {"section": section, "text": text}


def _send_discord_followup(
    interaction_token: str,
    content: str,
    components: Optional[List[Any]] = None,
    embeds: Optional[List[Any]] = None,
) -> None:
    """Edit the original deferred response via Discord's interaction follow-up webhook."""
    application_id = os.getenv('DISCORD_APPLICATION_ID')
    url = f"{DISCORD_API_BASE}/webhooks/{application_id}/{interaction_token}/messages/@original"
    payload: Dict[str, Any] = {"content": content}
    if components is not None:
        payload["components"] = components
    if embeds is not None:
        payload["embeds"] = embeds
    try:
        response = requests.patch(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Discord follow-up: {e}")


def _send_discord_new_message(
    interaction_token: str,
    content: str,
    components: Optional[List[Any]] = None,
    embeds: Optional[List[Any]] = None,
) -> None:
    """Post an additional follow-up message (not editing the original deferred one)."""
    application_id = os.getenv('DISCORD_APPLICATION_ID')
    url = f"{DISCORD_API_BASE}/webhooks/{application_id}/{interaction_token}"
    payload: Dict[str, Any] = {"content": content}
    if components is not None:
        payload["components"] = components
    if embeds is not None:
        payload["embeds"] = embeds
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Discord new message: {e}")


def _self_invoke_async(source: str, payload_extra: Dict[str, Any]) -> None:
    """Fire-and-forget self-invoke to do slow work outside Discord's 3s window."""
    function_name = os.getenv('AWS_LAMBDA_FUNCTION_NAME')
    if not function_name:
        logger.warning(f"AWS_LAMBDA_FUNCTION_NAME not set; skipping {source} invocation (local testing?)")
        return

    try:
        lambda_client = boto3.client('lambda')
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='Event',
            Payload=json.dumps({'source': source, **payload_extra}).encode('utf-8'),
        )
    except Exception as e:
        logger.error(f"Failed to invoke {source}: {e}")


def _handle_async_worker(event: Dict[str, Any]) -> Dict[str, Any]:
    """Do the real work for a deferred command, then notify Discord."""
    message: Optional[str] = event.get('message')
    interaction_token: Optional[str] = event.get('interaction_token')

    if not message or not interaction_token:
        logger.error("Async worker invoked without message or interaction_token")
        return {"statusCode": 400}

    result = _process_message(message)

    if result.get('intent') == 'new_lore':
        section = str(result.get('suggested_section', 'Unexplored Ideas'))
        lore_message = _build_lore_confirmation_message(message, section)
        _send_discord_followup(
            interaction_token,
            lore_message['content'],
            components=lore_message['components'],
            embeds=lore_message['embeds'],
        )
        return {"statusCode": 200}

    reply: str = str(result.get('content') or result.get('error', 'Something went wrong.'))
    content = f"> **{message}**\n\n{reply}"
    _send_discord_followup(interaction_token, content)
    return {"statusCode": 200}


def _handle_lore_worker(event: Dict[str, Any]) -> Dict[str, Any]:
    """Write confirmed lore to the canon doc, then notify Discord."""
    text: Optional[str] = event.get('text')
    section: Optional[str] = event.get('section')
    interaction_token: Optional[str] = event.get('interaction_token')

    if not text or not section or not interaction_token:
        logger.error("Lore worker invoked with missing text/section/interaction_token")
        return {"statusCode": 400}

    try:
        docs = GoogleDocsClient()
        docs.append_to_section(text, section)
        content = f"✅ Added to **{section}**."
    except Exception as e:
        logger.error(f"Failed to write lore to canon doc: {e}")
        content = "⚠️ Failed to save this to the canon doc. Please try again."

    _send_discord_followup(interaction_token, content, components=[], embeds=[])
    return {"statusCode": 200}


def _handle_component_interaction(parsed_event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle Confirm/Discard button clicks on a pending lore submission."""
    custom_id = parsed_event.get('custom_id')
    interaction_token = parsed_event.get('interaction_token')

    if custom_id == 'lore_discard':
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "type": 7,  # UPDATE_MESSAGE
                "data": {"content": "❌ Discarded.", "components": [], "embeds": []},
            }),
        }

    if custom_id == 'lore_confirm':
        parsed_lore = _parse_lore_message(parsed_event.get('message_embeds', []))
        if not parsed_lore:
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({
                    "type": 7,
                    "data": {
                        "content": "⚠️ Couldn't read this submission anymore. Please try /lore again.",
                        "components": [],
                        "embeds": [],
                    },
                }),
            }

        _self_invoke_async('discord_lore_worker', {
            'text': parsed_lore['text'],
            'section': parsed_lore['section'],
            'interaction_token': interaction_token,
        })

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"type": 6}),  # DEFERRED_UPDATE_MESSAGE
        }

    logger.warning(f"Unknown component custom_id: {custom_id}")
    return {
        "statusCode": 400,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "Unknown interaction"}),
    }


def _defer_and_process_async(parsed_event: Dict[str, Any]) -> Dict[str, Any]:
    """Acknowledge the interaction immediately, do the real work asynchronously."""
    _self_invoke_async('discord_async_worker', {
        'message': parsed_event.get('message'),
        'interaction_token': parsed_event.get('interaction_token'),
    })

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"type": 5}),  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    }


def _defer_and_process_song_refresh(parsed_event: Dict[str, Any]) -> Dict[str, Any]:
    """Acknowledge /refresh-songs immediately, run the Suno check asynchronously."""
    _self_invoke_async('discord_song_refresh_worker', {
        'interaction_token': parsed_event.get('interaction_token'),
    })

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"type": 5}),  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    }


def _handle_song_refresh_worker(event: Dict[str, Any]) -> Dict[str, Any]:
    """Check Suno profiles for new lore drops, echoing and offering to confirm each one."""
    interaction_token: Optional[str] = event.get('interaction_token')
    if not interaction_token:
        logger.error("Song refresh worker invoked without interaction_token")
        return {"statusCode": 400}

    try:
        result = suno_client.refresh()
    except Exception as e:
        logger.error(f"Song refresh failed: {e}")
        _send_discord_followup(interaction_token, "⚠️ Failed to check for new songs. Please try again.")
        return {"statusCode": 200}

    drops = result.get('new_lore_drops', [])
    logger.info(
        f"Song refresh: checked {result.get('profiles_checked')} profiles, "
        f"{result.get('clips_checked')} clips, found {len(drops)} new lore drops"
    )

    if drops:
        canon_doc: Optional[str] = None
        claude: Optional[ClaudeClient] = None
        try:
            claude = ClaudeClient()
            canon_doc = GoogleDocsClient().read_document()
        except Exception as e:
            logger.error(f"Failed to initialize clients for lore drop classification: {e}")

        for drop in drops:
            echo = (
                f"🎵 New reply from **{drop['handle']}** on *{drop.get('title', 'a song')}*:\n"
                f"> {drop['content']}"
            )

            intent = 'neither'
            if claude is not None and canon_doc is not None:
                try:
                    intent_result = claude.classify_intent(drop['content'], canon_doc)
                    intent = intent_result.get('intent', 'neither')
                except Exception as e:
                    logger.error(f"Failed to classify lore drop: {e}")
                    intent_result = {}
            else:
                intent_result = {}

            if intent == 'new_lore':
                section = str(intent_result.get('suggested_section', 'Unexplored Ideas'))
                lore_message = _build_lore_confirmation_message(drop['content'], section)
                _send_discord_new_message(
                    interaction_token,
                    echo,
                    embeds=lore_message['embeds'],
                    components=lore_message['components'],
                )
            else:
                _send_discord_new_message(interaction_token, echo)

    # Only persist the manifest after every drop has actually been posted.
    # If this Lambda times out or crashes mid-loop above, execution never
    # reaches here, so unprocessed drops stay unmarked and get found again
    # (and reposted) on the next refresh -- a duplicate is far better than
    # silently losing lore that was never surfaced.
    try:
        suno_client.save_manifest(result['manifest'])
    except Exception as e:
        logger.error(f"Failed to save Suno manifest: {e}")

    plural = "s" if len(drops) != 1 else ""
    summary = (
        f"🔄 Checked {result.get('profiles_checked', 0)} profile(s), "
        f"{result.get('clips_checked', 0)} song(s). "
        f"Found {len(drops)} new lore drop{plural}."
    )
    _send_discord_followup(interaction_token, summary)
    return {"statusCode": 200}


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

    if event.get('source') == 'discord_lore_worker':
        return _handle_lore_worker(event)

    if event.get('source') == 'discord_song_refresh_worker':
        return _handle_song_refresh_worker(event)

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

        # Handle button clicks (Confirm/Discard on a pending lore submission)
        if parsed_event.get('type') == 'component':
            return _handle_component_interaction(parsed_event)

        # /refresh-songs takes no text option, so it can't go through the
        # extract_message_from_event path below
        if parsed_event.get('command_name') == 'refresh-songs':
            return _defer_and_process_song_refresh(parsed_event)

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
