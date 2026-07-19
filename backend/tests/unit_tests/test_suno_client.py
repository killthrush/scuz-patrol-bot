"""Unit tests for the Suno scraping client (mocked HTTP + S3)."""

import json
from unittest.mock import Mock, patch

import pytest

from src import suno_client


class FakeNoSuchKey(Exception):
    """Stand-in for boto3's s3.exceptions.NoSuchKey."""

    pass


@pytest.fixture
def mock_requests_get():
    with patch("src.suno_client.requests.get") as mock_get:
        yield mock_get


class TestFetchHelpers:
    """Test the individual Suno API fetch functions."""

    def test_fetch_profile_builds_correct_url(self, mock_requests_get):
        mock_response = Mock()
        mock_response.json.return_value = {"num_total_clips": 1, "clips": []}
        mock_requests_get.return_value = mock_response

        result = suno_client.fetch_profile("scuz_patrol")

        called_url = mock_requests_get.call_args.args[0]
        assert "profiles/scuz_patrol" in called_url
        assert result["num_total_clips"] == 1

    def test_fetch_clip_builds_correct_url(self, mock_requests_get):
        mock_response = Mock()
        mock_response.json.return_value = {"id": "clip1", "title": "Incarcerator"}
        mock_requests_get.return_value = mock_response

        result = suno_client.fetch_clip("clip1")

        called_url = mock_requests_get.call_args.args[0]
        assert "clip/clip1" in called_url
        assert result["title"] == "Incarcerator"

    def test_fetch_comments_builds_correct_url(self, mock_requests_get):
        mock_response = Mock()
        mock_response.json.return_value = {"results": []}
        mock_requests_get.return_value = mock_response

        suno_client.fetch_comments("clip1")

        called_url = mock_requests_get.call_args.args[0]
        assert "gen/clip1/comments" in called_url


class TestRateLimitRetry:
    """Suno's API rate limits us -- verify we back off and retry instead of dropping data."""

    def test_retries_on_429_then_succeeds(self, mock_requests_get, monkeypatch):
        monkeypatch.setattr("src.suno_client.time.sleep", lambda _: None)

        rate_limited = Mock(status_code=429)
        success = Mock(status_code=200)
        success.json.return_value = {"clips": []}
        mock_requests_get.side_effect = [rate_limited, success]

        result = suno_client.fetch_profile("scuz_patrol")

        assert result == {"clips": []}
        assert mock_requests_get.call_count == 2

    def test_raises_after_exhausting_retries(self, mock_requests_get, monkeypatch):
        monkeypatch.setattr("src.suno_client.time.sleep", lambda _: None)

        import requests as real_requests

        rate_limited = Mock(status_code=429)
        rate_limited.raise_for_status.side_effect = real_requests.exceptions.HTTPError(
            "429"
        )
        mock_requests_get.return_value = rate_limited

        with pytest.raises(real_requests.exceptions.HTTPError):
            suno_client.fetch_profile("alfredokilgore")

        assert mock_requests_get.call_count == suno_client.MAX_RETRIES


class TestFetchProfilesParallel:
    """Test concurrent profile fetching."""

    def test_fetches_all_handles(self):
        def fake_fetch(handle):
            return {"handle": handle, "clips": []}

        with patch("src.suno_client.fetch_profile", side_effect=fake_fetch):
            results = suno_client.fetch_profiles_parallel(
                {"scuz_patrol", "alfredokilgore"}
            )

        assert set(results.keys()) == {"scuz_patrol", "alfredokilgore"}
        assert results["scuz_patrol"]["handle"] == "scuz_patrol"

    def test_one_failure_does_not_block_others(self):
        def fake_fetch(handle):
            if handle == "broken_handle":
                raise Exception("404")
            return {"handle": handle}

        with patch("src.suno_client.fetch_profile", side_effect=fake_fetch):
            results = suno_client.fetch_profiles_parallel(
                {"scuz_patrol", "broken_handle"}
            )

        assert "scuz_patrol" in results
        assert "broken_handle" not in results


class TestExtractNewCanonReplies:
    """Test lore-drop extraction from nested comment replies."""

    def test_extracts_reply_from_canon_handle(self):
        comments = {
            "results": [
                {
                    "id": "c1",
                    "content": "love this song",
                    "replies": [
                        {
                            "id": "r1",
                            "user_handle": "alfredokilgore",
                            "content": "thanks, wrote it in 2020",
                        }
                    ],
                }
            ]
        }

        new_replies = suno_client.extract_new_canon_replies(comments, seen_ids=set())

        assert len(new_replies) == 1
        assert new_replies[0]["handle"] == "alfredokilgore"
        assert new_replies[0]["content"] == "thanks, wrote it in 2020"
        assert new_replies[0]["parent_content"] == "love this song"

    def test_ignores_non_canon_handle(self):
        comments = {
            "results": [
                {
                    "id": "c1",
                    "content": "cool",
                    "replies": [
                        {"id": "r1", "user_handle": "randomfan", "content": "yeah"}
                    ],
                }
            ]
        }

        new_replies = suno_client.extract_new_canon_replies(comments, seen_ids=set())

        assert new_replies == []

    def test_ignores_already_seen_reply(self):
        comments = {
            "results": [
                {
                    "id": "c1",
                    "content": "cool",
                    "replies": [
                        {"id": "r1", "user_handle": "metrivus", "content": "yeah"}
                    ],
                }
            ]
        }

        new_replies = suno_client.extract_new_canon_replies(comments, seen_ids={"r1"})

        assert new_replies == []


class TestSongArtifactStorage:
    """Test S3-backed per-song artifact load/save (one JSON object per clip_id)."""

    def test_load_artifact_returns_parsed_json(self, monkeypatch):
        monkeypatch.setenv("MANIFEST_BUCKET", "test-bucket")
        mock_s3 = Mock()
        mock_body = Mock()
        mock_body.read.return_value = json.dumps({"comment_count": 2}).encode()
        mock_s3.get_object.return_value = {"Body": mock_body}

        with patch("src.suno_client.boto3.client", return_value=mock_s3):
            artifact = suno_client.load_song_artifact("clip1")

        assert artifact == {"comment_count": 2}
        mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="songs/clip1.json"
        )

    def test_load_artifact_returns_empty_dict_when_missing(self, monkeypatch):
        monkeypatch.setenv("MANIFEST_BUCKET", "test-bucket")
        mock_s3 = Mock()
        mock_s3.exceptions.NoSuchKey = FakeNoSuchKey
        mock_s3.get_object.side_effect = FakeNoSuchKey()

        with patch("src.suno_client.boto3.client", return_value=mock_s3):
            artifact = suno_client.load_song_artifact("clip1")

        assert artifact == {}

    def test_save_artifact_writes_json_to_own_key(self, monkeypatch):
        monkeypatch.setenv("MANIFEST_BUCKET", "test-bucket")
        mock_s3 = Mock()

        with patch("src.suno_client.boto3.client", return_value=mock_s3):
            suno_client.save_song_artifact("clip1", {"comment_count": 2})

        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "songs/clip1.json"
        assert json.loads(call_kwargs["Body"]) == {"comment_count": 2}

    def test_raises_without_manifest_bucket(self, monkeypatch):
        monkeypatch.delenv("MANIFEST_BUCKET", raising=False)

        with pytest.raises(ValueError, match="MANIFEST_BUCKET"):
            suno_client.load_song_artifact("clip1")


class TestMineSongFacts:
    """Test diffing one song's live clip/comments against its artifact for candidate facts."""

    def _clip(self, **overrides):
        clip = {
            "id": "clip1",
            "title": "Incarcerator",
            "handle": "scuz_patrol",
            "comment_count": 1,
            "caption": "Track 8",
            "metadata": {"prompt": "backstory + lyrics blob"},
        }
        clip.update(overrides)
        return clip

    def test_extracts_new_canon_reply_as_candidate(self):
        clip = self._clip(caption=None, metadata={})
        comments = {
            "results": [
                {
                    "id": "c1",
                    "content": "nice",
                    "replies": [
                        {
                            "id": "r1",
                            "user_handle": "alfredokilgore",
                            "content": "wrote this in prison",
                        }
                    ],
                }
            ]
        }

        candidates, updated_artifact = suno_client.mine_song_facts(clip, comments, {})

        assert len(candidates) == 1
        assert candidates[0] == {
            "content": "wrote this in prison",
            "handle": "alfredokilgore",
            "source": "suno_reply",
            "source_ref": "r1",
        }
        assert "r1" in updated_artifact["cached_comment_ids"]

    def test_extracts_new_caption_as_candidate(self):
        clip = self._clip(metadata={})
        comments = {"results": []}

        candidates, updated_artifact = suno_client.mine_song_facts(clip, comments, {})

        assert candidates == [
            {
                "content": "Track 8",
                "handle": "scuz_patrol",
                "source": "suno_caption",
                "source_ref": "clip1",
            }
        ]
        assert updated_artifact["caption"] == "Track 8"

    def test_unchanged_caption_is_not_a_candidate(self):
        clip = self._clip(metadata={})
        comments = {"results": []}
        artifact = {"caption": "Track 8"}

        candidates, _ = suno_client.mine_song_facts(clip, comments, artifact)

        assert candidates == []

    def test_extracts_new_lyrics_as_candidate(self):
        clip = self._clip(caption=None)
        comments = {"results": []}

        candidates, updated_artifact = suno_client.mine_song_facts(clip, comments, {})

        assert candidates == [
            {
                "content": "backstory + lyrics blob",
                "handle": "scuz_patrol",
                "source": "suno_lyrics",
                "source_ref": "clip1",
            }
        ]
        assert updated_artifact["lyrics"] == "backstory + lyrics blob"

    def test_unchanged_lyrics_is_not_a_candidate(self):
        clip = self._clip(caption=None)
        comments = {"results": []}
        artifact = {"lyrics": "backstory + lyrics blob"}

        candidates, _ = suno_client.mine_song_facts(clip, comments, artifact)

        assert candidates == []

    def test_updated_artifact_reflects_all_current_values_even_with_no_candidates(self):
        clip = self._clip()
        comments = {"results": []}
        artifact = {"caption": "Track 8", "lyrics": "backstory + lyrics blob"}

        candidates, updated_artifact = suno_client.mine_song_facts(
            clip, comments, artifact
        )

        assert candidates == []
        assert updated_artifact["comment_count"] == 1
        assert updated_artifact["caption"] == "Track 8"
        assert updated_artifact["lyrics"] == "backstory + lyrics blob"


class TestEnqueueSong:
    """Test enqueueing one song onto the FIFO song ingest queue."""

    def test_sends_message_with_single_group_and_dedup_id(self, monkeypatch):
        monkeypatch.setenv("SONG_QUEUE_URL", "https://sqs.example/test-queue.fifo")
        mock_sqs = Mock()

        with patch("src.suno_client.boto3.client", return_value=mock_sqs):
            suno_client.enqueue_song("clip1", "scuz_patrol", "Incarcerator")

        call_kwargs = mock_sqs.send_message.call_args.kwargs
        assert call_kwargs["QueueUrl"] == "https://sqs.example/test-queue.fifo"
        assert call_kwargs["MessageGroupId"] == "songs"
        assert call_kwargs["MessageDeduplicationId"] == "clip1"
        body = json.loads(call_kwargs["MessageBody"])
        assert body == {
            "clip_id": "clip1",
            "handle": "scuz_patrol",
            "title": "Incarcerator",
        }

    def test_raises_without_song_queue_url(self, monkeypatch):
        monkeypatch.delenv("SONG_QUEUE_URL", raising=False)

        with pytest.raises(ValueError, match="SONG_QUEUE_URL"):
            suno_client.enqueue_song("clip1", "scuz_patrol", "Incarcerator")
