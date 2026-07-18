#!/bin/bash
# Fetch one Suno song's clip metadata + comments into the local cache.
# Usage: fetch_song.sh <clip_id> [local_name]
# local_name defaults to clip_id if omitted.
set -euo pipefail

CLIP_ID="${1:?usage: fetch_song.sh <clip_id> [local_name]}"
LOCAL_NAME="${2:-$CLIP_ID}"
CACHE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/songs"

mkdir -p "$CACHE_DIR"

curl -s "https://studio-api-prod.suno.com/api/clip/$CLIP_ID" \
  -H 'User-Agent: Mozilla/5.0' \
  -o "$CACHE_DIR/$LOCAL_NAME.clip.json"

curl -s "https://studio-api-prod.suno.com/api/gen/$CLIP_ID/comments?order=newest" \
  -H 'User-Agent: Mozilla/5.0' \
  -o "$CACHE_DIR/$LOCAL_NAME.comments.json"

echo "Fetched $LOCAL_NAME ($CLIP_ID) -> $CACHE_DIR/$LOCAL_NAME.{clip,comments}.json"
