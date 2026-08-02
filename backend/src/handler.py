"""Lambda handler for Scuz Patrol Discord bot.

Receives Discord webhook events and either answers lore questions directly
from the fact store or records new lore into it. The fact store (DynamoDB)
is the sole source of truth -- there is no separate canon document.

Discord requires an interaction response within 3 seconds. Since Claude
calls can exceed that, slash commands are acknowledged immediately with a
deferred response (type 5), then processed by asynchronously self-invoking
this same Lambda. The async invocation does the real work and posts the
answer via Discord's follow-up webhook.
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
from src import suno_client
from src import fact_store
from src.calibration_queries import CALIBRATION_QUERIES

# Load .env for local testing (no-op in Lambda)
load_dotenv()

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_EMBED_DESCRIPTION_LIMIT = 4096


def _initialize_secrets() -> None:
    """Fetch secrets from Secrets Manager and set as env vars.

    Runs once at Lambda startup. If env vars already set (local testing),
    skips Secrets Manager fetch. This ensures all code paths use env vars.
    Gracefully handles local testing where Secrets Manager is unavailable.
    """
    secrets = {
        "ANTHROPIC_API_KEY": "scuz-patrol-bot-dev/anthropic-api-key",
        "DISCORD_BOT_TOKEN": "scuz-patrol-bot-dev/discord-token",
        "GOOGLE_SERVICE_ACCOUNT_KEY": "scuz-patrol-bot-dev/google-service-account",
        "DISCORD_PUBLIC_KEY": "scuz-patrol-bot-dev/discord-public-key",
        "DISCORD_APPLICATION_ID": "scuz-patrol-bot-dev/discord-application-id",
    }

    for env_var, secret_name in secrets.items():
        # Skip if already set (local testing with .env)
        if os.getenv(env_var):
            continue

        try:
            client = boto3.client("secretsmanager", region_name="us-east-1")
            response = client.get_secret_value(SecretId=secret_name)
            os.environ[env_var] = response["SecretString"]
            logger.debug(f"Loaded {env_var} from Secrets Manager")
        except Exception as e:
            # Gracefully skip if Secrets Manager is unavailable (local testing)
            logger.debug(
                f"Secrets Manager unavailable for {env_var} (expected in local testing): {e}"
            )


# Initialize secrets once at Lambda startup
_initialize_secrets()


def _build_lore_confirmation_message(
    text: str, section: str, submitted_by: str
) -> Dict[str, Any]:
    """Build a Discord message asking the user to confirm/discard new lore.

    The lore text lives in the embed description (4096-char cap), not the
    message content (2000-char cap) or an embed field (1024-char cap) --
    the length check in _handle_lore_intake_worker guarantees it always fits here.
    Section and submitter are still stored as structured embed fields (not
    interpolated into free text) so a later button click can recover them by
    reading exact field values — no delimiter for a user's own submitted
    text to spoof or corrupt, unlike parsing them back out of a formatted
    string.
    """
    embed = {
        "title": "🆕 New lore suggestion — add this to the canon?",
        "description": text,
        "fields": [
            {"name": "Section", "value": section},
            {"name": "Submitted by", "value": submitted_by},
        ],
    }
    components = [
        {
            "type": 1,  # ACTION_ROW
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "Confirm",
                    "custom_id": "lore_confirm",
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "Discard",
                    "custom_id": "lore_discard",
                },
            ],
        }
    ]
    return {"content": "", "embeds": [embed], "components": components}


def _build_lore_result_message(
    text: str, section: str, submitted_by: str, status_line: str
) -> Dict[str, Any]:
    """Build the post-click version of a lore confirmation message.

    Keeps the lore text (in the embed description) and Section/Submitted by
    fields visible instead of wiping them, so anyone scrolling back can
    still see what was confirmed or discarded (and by whom) rather than
    just a bare status line with no context.
    """
    embed = {
        "title": f"🆕 New lore suggestion — {status_line}",
        "description": text,
        "fields": [
            {"name": "Section", "value": section},
            {"name": "Submitted by", "value": submitted_by},
        ],
    }
    return {"content": "", "embeds": [embed], "components": []}


def _parse_lore_message(embeds: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Recover the lore text, section, and submitter from a confirmation message.

    Section/submitter come from embed fields, read by exact name so a
    user's own lore text has nothing to spoof; the lore text itself comes
    from the embed description, where _build_lore_confirmation_message put it.
    """
    if not embeds:
        return None

    fields = {f.get("name"): f.get("value") for f in embeds[0].get("fields", [])}
    section = fields.get("Section")
    text = embeds[0].get("description")
    if not section or not text:
        return None
    return {
        "section": section,
        "text": text,
        "submitted_by": fields.get("Submitted by") or "Unknown",
    }


def _send_discord_followup(
    interaction_token: str,
    content: str,
    components: Optional[List[Any]] = None,
    embeds: Optional[List[Any]] = None,
) -> None:
    """Edit the original deferred response via Discord's interaction follow-up webhook."""
    application_id = os.getenv("DISCORD_APPLICATION_ID")
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


def _self_invoke_async(source: str, payload_extra: Dict[str, Any]) -> None:
    """Fire-and-forget self-invoke to do slow work outside Discord's 3s window."""
    function_name = os.getenv("AWS_LAMBDA_FUNCTION_NAME")
    if not function_name:
        logger.warning(
            f"AWS_LAMBDA_FUNCTION_NAME not set; skipping {source} invocation (local testing?)"
        )
        return

    try:
        lambda_client = boto3.client("lambda")
        lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps({"source": source, **payload_extra}).encode("utf-8"),
        )
    except Exception as e:
        logger.error(f"Failed to invoke {source}: {e}")


def _defer_and_process_lore_intake(parsed_event: Dict[str, Any]) -> Dict[str, Any]:
    """Acknowledge /lore immediately, build the confirm/discard prompt asynchronously."""
    _self_invoke_async(
        "discord_lore_intake_worker",
        {
            "text": parsed_event.get("message"),
            "interaction_token": parsed_event.get("interaction_token"),
            "user_name": parsed_event.get("user_name"),
        },
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"type": 5}),  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    }


def _handle_lore_intake_worker(event: Dict[str, Any]) -> Dict[str, Any]:
    """File a /lore submission into a section and send the confirm/discard prompt.

    /lore already declares the submitted text as lore by virtue of being
    /lore -- unlike the old shared /ask+/lore pipeline, there's no
    lore/question/neither gate to run here, only "which section does this
    go in" (suggest_section).
    """
    text: Optional[str] = event.get("text")
    interaction_token: Optional[str] = event.get("interaction_token")
    submitted_by: str = event.get("user_name") or "Unknown"

    if not text or not interaction_token:
        logger.error("Lore intake worker invoked without text or interaction_token")
        return {"statusCode": 400}

    # Bounded by Discord's embed description cap, not fact_store.MAX_FACT_LENGTH
    # (which is sized for Suno-mined content that never touches Discord) --
    # the confirmation prompt has to round-trip this text through one embed.
    if len(text) > DISCORD_EMBED_DESCRIPTION_LIMIT:
        _send_discord_followup(
            interaction_token,
            f"⚠️ That's {len(text)} characters — lore submissions are capped at "
            f"{DISCORD_EMBED_DESCRIPTION_LIMIT}. Please shorten it and try again.",
        )
        return {"statusCode": 200}

    try:
        section = ClaudeClient().suggest_section(text)
    except Exception as e:
        logger.error(f"Failed to suggest a section for /lore submission: {e}")
        section = "Unexplored Ideas"

    lore_message = _build_lore_confirmation_message(text, section, submitted_by)
    _send_discord_followup(
        interaction_token,
        lore_message["content"],
        components=lore_message["components"],
        embeds=lore_message["embeds"],
    )
    return {"statusCode": 200}


def _handle_lore_worker(event: Dict[str, Any]) -> Dict[str, Any]:
    """Record confirmed lore as a fact, then notify Discord."""
    text: Optional[str] = event.get("text")
    section: Optional[str] = event.get("section")
    interaction_token: Optional[str] = event.get("interaction_token")
    submitted_by: str = event.get("submitted_by") or "Unknown"

    if not text or not section or not interaction_token:
        logger.error("Lore worker invoked with missing text/section/interaction_token")
        return {"statusCode": 400}

    try:
        fact_store.put_fact(
            content=text,
            section_hint=section,
            handle=submitted_by,
            source="discord_lore",
            source_ref=interaction_token,
            category=fact_store.CATEGORY_LORE,
        )
        status_line = f"✅ Recorded under **{section}**."
    except Exception as e:
        logger.error(f"Failed to record /lore submission as a fact: {e}")
        status_line = "⚠️ Failed to save this. Please try again."

    result_message = _build_lore_result_message(
        text, section, submitted_by, status_line
    )
    _send_discord_followup(
        interaction_token,
        result_message["content"],
        components=result_message["components"],
        embeds=result_message["embeds"],
    )
    return {"statusCode": 200}


def _handle_component_interaction(parsed_event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle Confirm/Discard button clicks on a pending lore submission."""
    custom_id = parsed_event.get("custom_id")
    interaction_token = parsed_event.get("interaction_token")

    parsed_lore = _parse_lore_message(parsed_event.get("message_embeds", []))

    if custom_id == "lore_discard":
        if parsed_lore:
            result_message = _build_lore_result_message(
                parsed_lore["text"],
                parsed_lore["section"],
                parsed_lore["submitted_by"],
                "❌ Discarded.",
            )
        else:
            result_message = {"content": "❌ Discarded.", "components": [], "embeds": []}
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "type": 7,  # UPDATE_MESSAGE
                    "data": result_message,
                }
            ),
        }

    if custom_id == "lore_confirm":
        if not parsed_lore:
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(
                    {
                        "type": 7,
                        "data": {
                            "content": "⚠️ Couldn't read this submission anymore. Please try /lore again.",
                            "components": [],
                            "embeds": [],
                        },
                    }
                ),
            }

        _self_invoke_async(
            "discord_lore_worker",
            {
                "text": parsed_lore["text"],
                "section": parsed_lore["section"],
                "submitted_by": parsed_lore["submitted_by"],
                "interaction_token": interaction_token,
            },
        )

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


def _defer_and_process_song_refresh(parsed_event: Dict[str, Any]) -> Dict[str, Any]:
    """Acknowledge /refresh-songs immediately, run the Suno check asynchronously."""
    _self_invoke_async(
        "discord_song_refresh_worker",
        {
            "interaction_token": parsed_event.get("interaction_token"),
        },
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"type": 5}),  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    }


def _enqueue_playlist_clips(playlist_id: str, playlist_name: str) -> int:
    """Fetch one playlist's full clip list and enqueue each song.

    Playlists can include songs posted under a different Suno account than
    the one that curated the playlist (features/collabs) -- e.g. a track by
    the real person behind Kilgore, posted under their own handle, that
    never shows up in scuz_patrol's own clips list. profile.get("playlists")
    only carries metadata (id/name/song_count), so this is a separate fetch.
    Songs already seen via a profile's own clips list are harmless to
    re-enqueue -- SQS FIFO dedupes by clip_id within its window, and beyond
    that, mine_song_facts diffs against the S3 artifact and finds nothing
    new.
    """
    try:
        playlist = suno_client.fetch_playlist(playlist_id)
    except Exception as e:
        logger.error(
            f"Failed to fetch Suno playlist '{playlist_name}' ({playlist_id}): {e}"
        )
        return 0

    queued = 0
    for entry in playlist.get("playlist_clips", []):
        clip = entry.get("clip", {})
        clip_id = clip.get("id")
        if not clip_id:
            continue
        try:
            suno_client.enqueue_song(
                clip_id, clip.get("handle", "unknown"), clip.get("title")
            )
            queued += 1
        except Exception as e:
            logger.error(f"Failed to enqueue playlist clip {clip_id}: {e}")
    return queued


def _handle_song_refresh_worker(event: Dict[str, Any]) -> Dict[str, Any]:
    """Enumerate Suno profiles (and their playlists) and enqueue each song
    for background ingest.

    Deliberately does no mining/classification/doc-writing itself -- that all
    happens per-song in _process_song_queue_message, triggered by the SQS
    queue one song at a time. This keeps the interactive command fast and
    means there's no batch of work large enough to risk a timeout mid-run.
    """
    interaction_token: Optional[str] = event.get("interaction_token")
    if not interaction_token:
        logger.error("Song refresh worker invoked without interaction_token")
        return {"statusCode": 400}

    try:
        profiles = suno_client.fetch_profiles_parallel(suno_client.PROFILE_HANDLES)
    except Exception as e:
        logger.error(f"Failed to fetch Suno profiles: {e}")
        _send_discord_followup(
            interaction_token, "⚠️ Failed to check for new songs. Please try again."
        )
        return {"statusCode": 200}

    queued = 0
    for handle, profile in profiles.items():
        for clip in profile.get("clips", []):
            try:
                suno_client.enqueue_song(clip["id"], handle, clip.get("title"))
                queued += 1
            except Exception as e:
                logger.error(f"Failed to enqueue Suno clip {clip.get('id')}: {e}")

        for playlist in profile.get("playlists", []):
            queued += _enqueue_playlist_clips(
                playlist.get("id"), playlist.get("name", "unknown")
            )

    plural = "s" if queued != 1 else ""
    summary = (
        f"🔄 Checked {len(profiles)} profile(s), queued {queued} song{plural} "
        "for background lore-mining."
    )
    _send_discord_followup(interaction_token, summary)
    return {"statusCode": 200}


def _defer_and_process_test(parsed_event: Dict[str, Any]) -> Dict[str, Any]:
    """Acknowledge /test immediately, run the calibration suite asynchronously."""
    _self_invoke_async(
        "discord_test_worker",
        {
            "interaction_token": parsed_event.get("interaction_token"),
        },
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"type": 5}),  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    }


def _handle_test_worker(event: Dict[str, Any]) -> Dict[str, Any]:
    """Run every calibration query against answer_question_from_facts, grade
    each answer's fact coverage, and report an aggregate score.

    This exists to catch aggregation-quality degradation -- an answer that
    reads as plausible while quietly dropping one of several facts it
    should have combined. See calibration_queries.py for how each query is
    anchored to real fact_ids.
    """
    interaction_token: Optional[str] = event.get("interaction_token")
    if not interaction_token:
        logger.error("Test worker invoked without interaction_token")
        return {"statusCode": 400}

    try:
        all_facts = fact_store.get_lore_facts()
    except Exception as e:
        logger.error(f"Failed to load facts for calibration run: {e}")
        _send_discord_followup(
            interaction_token,
            "⚠️ Failed to load facts for calibration. Please try again.",
        )
        return {"statusCode": 200}

    facts_by_id = {f["fact_id"]: f for f in all_facts}
    claude = ClaudeClient()

    lines = []
    query_scores = []
    for query in CALIBRATION_QUERIES:
        required_facts = [
            facts_by_id[fid] for fid in query["required_fact_ids"] if fid in facts_by_id
        ]
        missing = len(query["required_fact_ids"]) - len(required_facts)

        try:
            answer = claude.answer_question_from_facts(query["question"], all_facts)
            grading = claude.grade_answer_coverage(
                query["question"],
                answer,
                required_facts,
                speculative=query.get("speculative", False),
            )
        except Exception as e:
            logger.error(f"Calibration query '{query['query_id']}' failed: {e}")
            lines.append(f"⚠️ **{query['query_id']}**: failed ({e})")
            continue

        score = grading["score"]
        query_scores.append(score)
        pct = round(score * 100)
        note = f" ({missing} required fact(s) not found in store)" if missing else ""
        lines.append(f"**{query['query_id']}** — {pct}%{note}")
        logger.info(
            f"Calibration '{query['query_id']}' ({pct}%) answer: {answer}\n"
            f"Grading: {json.dumps(grading['results'])}"
        )

    overall = (
        round((sum(query_scores) / len(query_scores)) * 100) if query_scores else 0
    )
    summary = (
        f"🧪 **Calibration results** — overall fact coverage: {overall}%\n\n"
        + "\n".join(lines)
    )
    _send_discord_followup(interaction_token, summary)
    return {"statusCode": 200}


def _defer_and_process_ask(parsed_event: Dict[str, Any]) -> Dict[str, Any]:
    """Acknowledge /ask immediately, answer from the fact store asynchronously."""
    _self_invoke_async(
        "discord_ask_worker",
        {
            "message": parsed_event.get("message"),
            "interaction_token": parsed_event.get("interaction_token"),
        },
    )

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"type": 5}),  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    }


def _handle_ask_worker(event: Dict[str, Any]) -> Dict[str, Any]:
    """Answer a question directly from the fact store -- the fact store is
    the sole source of truth, there is no separate canon document."""
    message: Optional[str] = event.get("message")
    interaction_token: Optional[str] = event.get("interaction_token")

    if not message or not interaction_token:
        logger.error("Ask worker invoked without message or interaction_token")
        return {"statusCode": 400}

    try:
        all_facts = fact_store.get_lore_facts()
        claude = ClaudeClient()
        answer = claude.answer_question_from_facts(message, all_facts)
    except Exception as e:
        logger.error(f"Failed to answer /ask question: {e}")
        _send_discord_followup(
            interaction_token, "⚠️ Failed to generate an answer. Please try again."
        )
        return {"statusCode": 200}

    # Discord caps plain message content at 2000 chars -- fact-store-direct
    # answers routinely exceed that (a full bio easily runs past it), which
    # made the follow-up PATCH 400 and left the "thinking..." message
    # hanging forever with no visible error. An embed description allows up
    # to 4096, which fits real answers instead of needing a hard truncation.
    if len(answer) > DISCORD_EMBED_DESCRIPTION_LIMIT:
        answer = answer[: DISCORD_EMBED_DESCRIPTION_LIMIT - 1] + "…"
    _send_discord_followup(
        interaction_token, f"> **{message}**", embeds=[{"description": answer}]
    )
    return {"statusCode": 200}


def _put_candidate_fact(candidate: Dict[str, Any], section: str) -> None:
    try:
        fact_store.put_fact(
            content=candidate["content"],
            section_hint=section,
            handle=candidate["handle"],
            source=candidate["source"],
            source_ref=candidate["source_ref"],
            category=candidate["category"],
            title=candidate.get("title"),
        )
    except ValueError as e:
        logger.warning(f"Skipping oversized {candidate['source']} fact: {e}")


def _write_candidate(
    candidate: Dict[str, Any],
    clip_id: str,
    claude: Optional[ClaudeClient],
) -> None:
    """Classify (if needed) and record one mined candidate as a fact.

    Lyrics/credit candidates never need classification -- they're recorded
    as-is. Lore from scuz_patrol/alfredokilgore (always_canon) skips the
    lore/question/neither gate entirely since those accounts never speak out
    of character -- there's no "is this lore?" question to ask them, so
    Claude is only asked "which section?" (suggest_section), never whether
    the content counts as lore at all. Everything else goes through the
    normal lore/question/neither gate.
    """
    if candidate["category"] != fact_store.CATEGORY_LORE:
        section = (
            "Lyrics Archive"
            if candidate["category"] == fact_store.CATEGORY_LYRICS
            else "Credits"
        )
        _put_candidate_fact(candidate, section)
        return

    if candidate.get("always_canon"):
        section = "Unexplored Ideas"
        if claude is not None:
            try:
                section = claude.suggest_section(candidate["content"])
            except Exception as e:
                logger.error(
                    f"Failed to get section suggestion for {candidate['source']} "
                    f"candidate for song {clip_id}: {e}"
                )
        _put_candidate_fact(candidate, section)
        return

    if claude is None:
        return
    try:
        classification = claude.classify_intent(candidate["content"])
    except Exception as e:
        logger.error(
            f"Failed to classify {candidate['source']} candidate for song {clip_id}: {e}"
        )
        return

    if classification.get("intent") != "new_lore":
        return

    section = str(classification.get("suggested_section", "Unexplored Ideas"))
    _put_candidate_fact(candidate, section)


def _process_song_queue_message(message: Dict[str, Any]) -> None:
    """Mine one song's comments/caption/lyrics for new lore and record any as facts.

    No Discord feedback here -- SQS messages aren't tied to a live interaction,
    and the caller (a human running /refresh-songs) already got an immediate
    "queued" reply. Facts land straight in the fact store with no confirm step.
    """
    clip_id = message["clip_id"]

    clip = suno_client.fetch_clip(clip_id)
    comments = suno_client.fetch_comments(clip_id)
    artifact = suno_client.load_song_artifact(clip_id)
    candidates, updated_artifact = suno_client.mine_song_facts(clip, comments, artifact)

    claude: Optional[ClaudeClient] = None
    if any(c["category"] == fact_store.CATEGORY_LORE for c in candidates):
        try:
            claude = ClaudeClient()
        except Exception as e:
            logger.error(f"Failed to init Claude client for song {clip_id}: {e}")

    for candidate in candidates:
        _write_candidate(candidate, clip_id, claude)

    suno_client.save_song_artifact(clip_id, updated_artifact)


def _handle_sqs_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Process SQS-triggered song ingest messages (batch size 1, one at a time).

    Lets exceptions propagate -- with batch size 1, a failure should make the
    message visible again for retry (and eventually land in the DLQ) rather
    than being silently dropped, consistent with this project's existing
    preference for duplicate processing over silent data loss.
    """
    for record in records:
        message = json.loads(record["body"])
        _process_song_queue_message(message)
    return {"statusCode": 200}


def _defer_extracting_message(
    parsed_event: Dict[str, Any], defer_fn: Any
) -> Dict[str, Any]:
    """Shared plumbing for commands that take a single text option: extract
    the message, 400 if missing, otherwise hand off to the given defer_fn."""
    message = extract_message_from_event(parsed_event)
    if not message:
        logger.warning("No message extracted from event")
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "No message found"}),
        }
    parsed_event["message"] = message
    return defer_fn(parsed_event)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda entry point.

    Args:
        event: API Gateway event (contains Discord webhook POST body), or an
            async self-invocation payload (source == 'discord_ask_worker', etc.)
        context: Lambda context

    Returns:
        HTTP response with status code and body
    """
    # SQS event source invocations carry a "Records" list instead of an API
    # Gateway/Discord payload -- recognize those before anything else.
    if "Records" in event:
        return _handle_sqs_records(event["Records"])

    # This Lambda self-invokes asynchronously to do slow work outside
    # Discord's 3-second interaction window; recognize those invocations.
    if event.get("source") == "discord_lore_intake_worker":
        return _handle_lore_intake_worker(event)

    if event.get("source") == "discord_lore_worker":
        return _handle_lore_worker(event)

    if event.get("source") == "discord_song_refresh_worker":
        return _handle_song_refresh_worker(event)

    if event.get("source") == "discord_test_worker":
        return _handle_test_worker(event)

    if event.get("source") == "discord_ask_worker":
        return _handle_ask_worker(event)

    try:
        logger.info(f"Received event: {json.dumps(event)}")

        # Parse the Discord webhook event
        parsed_event = parse_discord_event(event)
        logger.info(f"Parsed event type: {parsed_event.get('type')}")

        # Reject requests with invalid signatures
        if parsed_event.get("type") == "invalid_signature":
            return {
                "statusCode": 401,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Invalid signature"}),
            }

        # Handle ping challenges from Discord
        if parsed_event.get("type") == "ping":
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(parsed_event.get("respond_with", {})),
            }

        # Handle button clicks (Confirm/Discard on a pending lore submission)
        if parsed_event.get("type") == "component":
            return _handle_component_interaction(parsed_event)

        # /refresh-songs and /test take no text option, so they can't go
        # through _defer_extracting_message below
        if parsed_event.get("command_name") == "refresh-songs":
            return _defer_and_process_song_refresh(parsed_event)

        if parsed_event.get("command_name") == "test":
            return _defer_and_process_test(parsed_event)

        if parsed_event.get("command_name") == "ask":
            return _defer_extracting_message(parsed_event, _defer_and_process_ask)

        if parsed_event.get("command_name") == "lore":
            return _defer_extracting_message(
                parsed_event, _defer_and_process_lore_intake
            )

        logger.warning(f"Unhandled command: {parsed_event.get('command_name')}")
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Unknown command"}),
        }

    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
        }
