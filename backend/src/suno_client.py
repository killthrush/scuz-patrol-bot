"""Suno scraping client: finds new songs/lore drops from canon-voice profiles.

Ports the local scripts/{check_profile,fetch_song,rebuild_manifest,dump_replies}.py
pipeline into something callable from the Lambda. Two differences from the
local scripts:

- Uses `requests` directly instead of shelling out to curl (Lambda's base
  image doesn't reliably have curl, and requests already works fine here).
- The manifest lives in S3 instead of a local JSON file, since Lambda's /tmp
  isn't durable across invocations.

Canon-voice accounts frequently drop real worldbuilding lore as a REPLY to a
fan comment rather than as a top-level comment -- see dump_replies.py's
docstring. This module's new-lore detection follows the same pattern.
"""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set

import boto3
import requests  # type: ignore

logger = logging.getLogger()

SUNO_API_BASE = "https://studio-api-prod.suno.com/api"
SUNO_HEADERS = {"User-Agent": "Mozilla/5.0"}
REQUEST_TIMEOUT = 15

CANON_HANDLES = {"scuz_patrol", "alfredokilgore", "metrivus", "killthrush", "lubonit84"}

MANIFEST_KEY = "manifest.json"


def fetch_profile(handle: str) -> Dict[str, Any]:
    """Fetch a Suno profile's clips (id, title, comment_count, ...)."""
    url = (
        f"{SUNO_API_BASE}/profiles/{handle}"
        "?playlists_sort_by=upvote_count&clips_sort_by=created_at"
    )
    response = requests.get(url, headers=SUNO_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_clip(clip_id: str) -> Dict[str, Any]:
    """Fetch one clip's metadata."""
    url = f"{SUNO_API_BASE}/clip/{clip_id}"
    response = requests.get(url, headers=SUNO_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_comments(clip_id: str) -> Dict[str, Any]:
    """Fetch one clip's comments (including nested replies)."""
    url = f"{SUNO_API_BASE}/gen/{clip_id}/comments?order=newest"
    response = requests.get(url, headers=SUNO_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_profiles_parallel(handles: Set[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch multiple Suno profiles concurrently. Skips (and logs) failures."""
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


def find_flagged_clips(
    profiles: Dict[str, Dict[str, Any]], manifest: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return clips that are new or whose live comment_count differs from the manifest."""
    flagged = []
    for handle, profile in profiles.items():
        for clip in profile.get("clips", []):
            clip_id = clip["id"]
            live_count = clip.get("comment_count", 0)
            cached = manifest.get(clip_id)
            if cached is None:
                flagged.append({"clip_id": clip_id, "handle": handle, "title": clip.get("title")})
            elif live_count != cached.get("comment_count", 0):
                flagged.append({"clip_id": clip_id, "handle": handle, "title": clip.get("title")})
    return flagged


def fetch_clip_data_parallel(clip_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch clip metadata + comments for multiple clips concurrently."""
    results: Dict[str, Dict[str, Any]] = {}
    if not clip_ids:
        return results

    def _fetch_one(clip_id: str):
        return clip_id, fetch_clip(clip_id), fetch_comments(clip_id)

    with ThreadPoolExecutor(max_workers=min(len(clip_ids), 8)) as executor:
        futures = [executor.submit(_fetch_one, cid) for cid in clip_ids]
        for future in as_completed(futures):
            try:
                clip_id, clip, comments = future.result()
                results[clip_id] = {"clip": clip, "comments": comments}
            except Exception as e:
                logger.error(f"Failed to fetch clip data: {e}")
    return results


def extract_new_canon_replies(comments: Dict[str, Any], seen_ids: Set[str]) -> List[Dict[str, Any]]:
    """Extract replies from canon-voice handles not already in `seen_ids`."""
    new_replies = []
    for comment in comments.get("results", []):
        for reply in comment.get("replies", []):
            if reply.get("user_handle") not in CANON_HANDLES:
                continue
            if reply["id"] in seen_ids:
                continue
            new_replies.append({
                "reply_id": reply["id"],
                "handle": reply.get("user_handle"),
                "content": reply.get("content"),
                "created_at": reply.get("created_at"),
                "parent_content": comment.get("content"),
            })
    return new_replies


def _all_comment_ids(comments: Dict[str, Any]) -> List[str]:
    """Flatten top-level + nested reply comment ids (for the manifest cache)."""
    ids = []
    for comment in comments.get("results", []):
        ids.append(comment["id"])
        for reply in comment.get("replies", []):
            ids.append(reply["id"])
    return ids


def load_manifest() -> Dict[str, Any]:
    """Load the manifest from S3, or an empty dict if it doesn't exist yet."""
    bucket = os.getenv('MANIFEST_BUCKET')
    if not bucket:
        raise ValueError("MANIFEST_BUCKET not set")

    s3 = boto3.client('s3')
    try:
        response = s3.get_object(Bucket=bucket, Key=MANIFEST_KEY)
        return json.loads(response['Body'].read())
    except s3.exceptions.NoSuchKey:
        return {}


def save_manifest(manifest: Dict[str, Any]) -> None:
    """Save the manifest to S3."""
    bucket = os.getenv('MANIFEST_BUCKET')
    if not bucket:
        raise ValueError("MANIFEST_BUCKET not set")

    s3 = boto3.client('s3')
    s3.put_object(
        Bucket=bucket,
        Key=MANIFEST_KEY,
        Body=json.dumps(manifest, indent=2).encode('utf-8'),
    )


def refresh(handles: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Check canon-voice Suno profiles for new songs/lore drops, updating the manifest.

    Fetches all profiles in parallel, diffs against the manifest to find only
    what's new or changed, fetches those clips' full data in parallel, then
    extracts new lore drops (replies from canon-voice handles not seen before).

    Returns:
        {
            "profiles_checked": int,
            "clips_checked": int,
            "new_lore_drops": [{"clip_id", "title", "handle", "reply_id", "content",
                                 "parent_content", "created_at"}],
        }
    """
    handles = handles or CANON_HANDLES
    manifest = load_manifest()

    profiles = fetch_profiles_parallel(handles)
    flagged = find_flagged_clips(profiles, manifest)
    clip_data = fetch_clip_data_parallel([f["clip_id"] for f in flagged])

    new_lore_drops = []
    for flag in flagged:
        clip_id = flag["clip_id"]
        data = clip_data.get(clip_id)
        if not data:
            continue

        clip = data["clip"]
        comments = data["comments"]

        cached = manifest.get(clip_id, {})
        seen_ids = set(cached.get("cached_comment_ids", []))

        for reply in extract_new_canon_replies(comments, seen_ids):
            new_lore_drops.append({"clip_id": clip_id, "title": clip.get("title"), **reply})

        manifest[clip_id] = {
            "title": clip.get("title"),
            "handle": clip.get("handle"),
            "created_at": clip.get("created_at"),
            "comment_count": clip.get("comment_count", 0),
            "cached_comment_ids": _all_comment_ids(comments),
        }

    save_manifest(manifest)

    return {
        "profiles_checked": len(profiles),
        "clips_checked": len(flagged),
        "new_lore_drops": new_lore_drops,
    }
