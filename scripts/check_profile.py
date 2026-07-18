#!/usr/bin/env python3
"""Check a Suno profile for songs/comments that aren't reflected in the cache yet.

Usage: check_profile.py <handle> [<handle> ...]
e.g.:  check_profile.py scuz_patrol alfredokilgore

Prints:
  - clip ids on the profile that are missing from manifest.json entirely (new songs)
  - clip ids already cached whose live comment_count differs from the cached
    value (candidates to re-fetch comments for)

Does not fetch or write anything -- read-only. Use fetch_song.sh for anything
this flags as needing a refresh, then rerun rebuild_manifest.py.
"""
import json
import os
import sys
import subprocess

MANIFEST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manifest.json"
)


def fetch_profile(handle):
    # urllib's https handling is broken in some environments here -- shell out to curl instead.
    url = (
        f"https://studio-api-prod.suno.com/api/profiles/{handle}"
        "?playlists_sort_by=upvote_count&clips_sort_by=created_at"
    )
    result = subprocess.run(
        ["curl", "-s", url, "-H", "User-Agent: Mozilla/5.0"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def main():
    handles = sys.argv[1:]
    if not handles:
        print("usage: check_profile.py <handle> [<handle> ...]", file=sys.stderr)
        sys.exit(1)

    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)

    for handle in handles:
        profile = fetch_profile(handle)
        print(f"=== {handle} ({profile.get('num_total_clips')} total clips) ===")
        for clip in profile.get("clips", []):
            clip_id = clip["id"]
            title = clip.get("title")
            live_count = clip.get("comment_count", 0)
            if clip_id not in manifest:
                print(f"  NEW SONG: {title!r} ({clip_id})")
            else:
                cached_count = manifest[clip_id].get("comment_count", 0)
                if live_count != cached_count:
                    print(
                        f"  COMMENTS CHANGED: {title!r} ({clip_id}) "
                        f"cached={cached_count} live={live_count}"
                    )


if __name__ == "__main__":
    main()
