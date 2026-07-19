"""Suno scraping client: finds new songs/lore drops from canon-voice profiles.

Ports the local scripts/{check_profile,fetch_song,rebuild_manifest,dump_replies}.py
pipeline into something callable from the Lambda. Differences from the local
scripts:

- Uses `requests` directly instead of shelling out to curl (Lambda's base
  image doesn't reliably have curl, and requests already works fine here).
- Processing happens one song at a time, off an SQS queue (see handler.py's
  song-queue worker) rather than fetching everything in one Lambda invocation.
  Suno appears to rate-limit/flag entire IP ranges rather than just high
  per-caller volume, so the fix isn't a smarter retry loop -- it's never
  hammering the API from one invocation in the first place.
- Each song gets its own JSON artifact in S3 (clip_id -> last-seen comment
  ids/caption/lyrics) instead of one global manifest, so a bad or slow song
  can't block/corrupt tracking for every other song.

Canon-voice accounts frequently drop real worldbuilding lore as a REPLY to a
fan comment rather than as a top-level comment -- see dump_replies.py's
docstring. This module's new-lore detection follows the same pattern, and
also mines each clip's caption and "lyric box" (Suno bundles a written
backstory blurb and the actual lyrics into one `metadata.prompt` field),
since canon-voice accounts frequently hide lore there too.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set, Tuple

import boto3
import requests  # type: ignore

logger = logging.getLogger()

SUNO_API_BASE = "https://studio-api-prod.suno.com/api"
SUNO_HEADERS = {"User-Agent": "Mozilla/5.0"}
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3

# Suno profiles that actually post songs -- these are the ones checked for
# new clips. scuz_patrol is the band's account; alfredokilgore is an
# in-character account. The other canon-voice handles below are the real
# artists behind the project (pixie/metrivus, killthrush) -- they comment
# and drop lore, but don't have their own song profiles to scan.
PROFILE_HANDLES = {"scuz_patrol", "alfredokilgore"}

# Accounts whose REPLIES count as authoritative lore drops, regardless of
# which profile's song they're replying under.
CANON_HANDLES = {"scuz_patrol", "alfredokilgore", "metrivus", "killthrush", "lubonit84"}

SONG_ARTIFACT_PREFIX = "songs/"


def _get_json(url: str) -> Dict[str, Any]:
    """GET a Suno API URL, retrying with backoff on rate limiting (429)."""
    for attempt in range(MAX_RETRIES):
        response = requests.get(url, headers=SUNO_HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code == 429 and attempt < MAX_RETRIES - 1:
            wait = 2**attempt
            logger.warning(f"Rate limited fetching {url}, retrying in {wait}s")
            time.sleep(wait)
            continue
        response.raise_for_status()
        return response.json()
    raise AssertionError("unreachable")  # loop always returns or raises


def fetch_profile(handle: str) -> Dict[str, Any]:
    """Fetch a Suno profile's clips (id, title, comment_count, ...)."""
    url = (
        f"{SUNO_API_BASE}/profiles/{handle}"
        "?playlists_sort_by=upvote_count&clips_sort_by=created_at"
    )
    return _get_json(url)


def fetch_clip(clip_id: str) -> Dict[str, Any]:
    """Fetch one clip's metadata (title, caption, metadata.prompt, comment_count, ...)."""
    return _get_json(f"{SUNO_API_BASE}/clip/{clip_id}")


def fetch_comments(clip_id: str) -> Dict[str, Any]:
    """Fetch one clip's comments (including nested replies)."""
    return _get_json(f"{SUNO_API_BASE}/gen/{clip_id}/comments?order=newest")


def fetch_profiles_parallel(handles: Set[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch multiple Suno profiles concurrently. Skips (and logs) failures.

    Only PROFILE_HANDLES (2 accounts) are ever fetched this way, so the
    concurrency here is small and unrelated to per-song processing, which is
    deliberately serialized (see handler.py's SQS song queue).
    """
    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(handles) or 1) as executor:
        future_to_handle = {executor.submit(fetch_profile, h): h for h in handles}
        for future in as_completed(future_to_handle):
            handle = future_to_handle[future]
            try:
                results[handle] = future.result()
            except Exception as e:
                logger.error(f"Failed to fetch Suno profile '{handle}': {e}")
    return results


def extract_new_canon_replies(
    comments: Dict[str, Any], seen_ids: Set[str]
) -> List[Dict[str, Any]]:
    """Extract replies from canon-voice handles not already in `seen_ids`."""
    new_replies = []
    for comment in comments.get("results", []):
        for reply in comment.get("replies", []):
            if reply.get("user_handle") not in CANON_HANDLES:
                continue
            if reply["id"] in seen_ids:
                continue
            new_replies.append(
                {
                    "reply_id": reply["id"],
                    "handle": reply.get("user_handle"),
                    "content": reply.get("content"),
                    "created_at": reply.get("created_at"),
                    "parent_content": comment.get("content"),
                }
            )
    return new_replies


def _all_comment_ids(comments: Dict[str, Any]) -> List[str]:
    """Flatten top-level + nested reply comment ids (for the song artifact cache)."""
    ids = []
    for comment in comments.get("results", []):
        ids.append(comment["id"])
        for reply in comment.get("replies", []):
            ids.append(reply["id"])
    return ids


def _song_artifact_key(clip_id: str) -> str:
    return f"{SONG_ARTIFACT_PREFIX}{clip_id}.json"


def load_song_artifact(clip_id: str) -> Dict[str, Any]:
    """Load one song's tracking artifact from S3, or an empty dict if not seen before."""
    bucket = os.getenv("MANIFEST_BUCKET")
    if not bucket:
        raise ValueError("MANIFEST_BUCKET not set")

    s3 = boto3.client("s3")
    try:
        response = s3.get_object(Bucket=bucket, Key=_song_artifact_key(clip_id))
        return json.loads(response["Body"].read())
    except s3.exceptions.NoSuchKey:
        return {}


def save_song_artifact(clip_id: str, artifact: Dict[str, Any]) -> None:
    """Save one song's tracking artifact to S3."""
    bucket = os.getenv("MANIFEST_BUCKET")
    if not bucket:
        raise ValueError("MANIFEST_BUCKET not set")

    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=_song_artifact_key(clip_id),
        Body=json.dumps(artifact, indent=2).encode("utf-8"),
    )


def mine_song_facts(
    clip: Dict[str, Any], comments: Dict[str, Any], artifact: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Diff one song's live data against its last-seen artifact for candidate facts.

    Checks three sources: canon-voice comment replies (as before), the clip's
    caption, and its "lyric box" (metadata.prompt -- Suno bundles a written
    backstory blurb and the actual lyrics into this one field; not split
    further here since the delimiter between them isn't reliable enough to
    parse). Each candidate still needs classification by the caller (this
    module has no Claude dependency) before being written to the fact store.

    Returns:
        (candidates, updated_artifact) where each candidate is
        {"content", "handle", "source", "source_ref"} and updated_artifact
        should be saved regardless of whether any candidate became a fact,
        so unchanged content isn't re-checked on the next pass.
    """
    clip_id = clip["id"]
    candidates: List[Dict[str, Any]] = []

    seen_ids = set(artifact.get("cached_comment_ids", []))
    for reply in extract_new_canon_replies(comments, seen_ids):
        candidates.append(
            {
                "content": reply["content"],
                "handle": reply["handle"],
                "source": "suno_reply",
                "source_ref": reply["reply_id"],
            }
        )

    caption = clip.get("caption")
    if caption and caption != artifact.get("caption"):
        candidates.append(
            {
                "content": caption,
                "handle": clip.get("handle"),
                "source": "suno_caption",
                "source_ref": clip_id,
            }
        )

    lyrics = clip.get("metadata", {}).get("prompt")
    if lyrics and lyrics != artifact.get("lyrics"):
        candidates.append(
            {
                "content": lyrics,
                "handle": clip.get("handle"),
                "source": "suno_lyrics",
                "source_ref": clip_id,
            }
        )

    updated_artifact = {
        "title": clip.get("title"),
        "handle": clip.get("handle"),
        "comment_count": clip.get("comment_count", 0),
        "cached_comment_ids": _all_comment_ids(comments),
        "caption": caption,
        "lyrics": lyrics,
    }

    return candidates, updated_artifact


def enqueue_song(clip_id: str, handle: str, title: Optional[str]) -> None:
    """Enqueue one song for background ingest processing via the SQS song queue.

    All messages share one MessageGroupId so the FIFO queue processes songs
    strictly one at a time. MessageDeduplicationId is the clip_id so running
    /refresh-songs twice in quick succession doesn't double-queue the same song.
    """
    queue_url = os.getenv("SONG_QUEUE_URL")
    if not queue_url:
        raise ValueError("SONG_QUEUE_URL not set")

    sqs = boto3.client("sqs")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({"clip_id": clip_id, "handle": handle, "title": title}),
        MessageGroupId="songs",
        MessageDeduplicationId=clip_id,
    )
