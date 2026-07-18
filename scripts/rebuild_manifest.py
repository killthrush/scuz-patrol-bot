#!/usr/bin/env python3
"""Rebuild manifest.json from the songs/ cache.

Indexes clip_id -> {local_name, title, handle, created_at, comment_count,
cached_comment_ids}. cached_comment_ids includes BOTH top-level comment ids
and nested reply ids (results[].replies[].id) -- do not flatten only the
top level, or new-reply detection breaks.

Run this after fetch_song.sh adds/updates any song in the cache.
"""
import json
import os
import glob

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "songs")
MANIFEST_PATH = os.path.join(os.path.dirname(CACHE_DIR), "manifest.json")


def main():
    manifest = {}
    for clip_path in sorted(glob.glob(f"{CACHE_DIR}/*.clip.json")):
        name = os.path.basename(clip_path).replace(".clip.json", "")
        with open(clip_path) as f:
            clip = json.load(f)

        comments_path = f"{CACHE_DIR}/{name}.comments.json"
        comment_ids = []
        if os.path.exists(comments_path):
            with open(comments_path) as f:
                com = json.load(f)
            for c in com.get("results", []):
                comment_ids.append(c["id"])
                for r in c.get("replies", []):
                    comment_ids.append(r["id"])

        manifest[clip.get("id", name)] = {
            "local_name": name,
            "title": clip.get("title"),
            "handle": clip.get("handle"),
            "created_at": clip.get("created_at"),
            "comment_count": clip.get("comment_count", 0),
            "cached_comment_ids": comment_ids,
        }

    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Wrote manifest with {len(manifest)} songs to {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
