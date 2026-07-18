#!/usr/bin/env python3
"""Dump comment replies from the cache, with parent context.

Suno's comment API nests replies one level under results[].replies[]. The
canon-voice accounts (scuz_patrol, alfredokilgore, metrivus, killthrush,
lubonit84) frequently drop real worldbuilding lore as a REPLY to a fan
comment rather than as a top-level comment -- a top-level-only scan misses
this. This script exists so that never happens again silently.

Usage:
  dump_replies.py                 # all replies, every song
  dump_replies.py --canon-only    # only replies from the canon-voice accounts
  dump_replies.py <local_name>    # limit to one cached song (by filename stem)
"""
import json
import os
import sys
import glob

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "songs")

CANON_HANDLES = {"scuz_patrol", "alfredokilgore", "metrivus", "killthrush", "lubonit84"}


def main():
    args = sys.argv[1:]
    canon_only = "--canon-only" in args
    names = [a for a in args if not a.startswith("--")]

    pattern = f"{CACHE_DIR}/{names[0]}.comments.json" if names else f"{CACHE_DIR}/*.comments.json"

    for path in sorted(glob.glob(pattern)):
        name = os.path.basename(path).replace(".comments.json", "")
        with open(path) as f:
            data = json.load(f)

        pairs = [(c, r) for c in data.get("results", []) for r in c.get("replies", [])]
        if canon_only:
            pairs = [(c, r) for c, r in pairs if r.get("user_handle") in CANON_HANDLES]
        if not pairs:
            continue

        print("=" * 80)
        print(name.upper(), f"({len(pairs)} replies)")
        for parent, r in pairs:
            print(f"  on [{parent.get('user_display_name')}: {parent.get('content', '')[:50]!r}]")
            print(f"  -> [{r.get('user_handle')} | {r.get('created_at')}]: {r.get('content')}\n")


if __name__ == "__main__":
    main()
