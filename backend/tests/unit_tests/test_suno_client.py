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


class TestFetchProfilesParallel:
    """Test concurrent profile fetching."""

    def test_fetches_all_handles(self):
        def fake_fetch(handle):
            return {"handle": handle, "clips": []}

        with patch("src.suno_client.fetch_profile", side_effect=fake_fetch):
            results = suno_client.fetch_profiles_parallel({"scuz_patrol", "alfredokilgore"})

        assert set(results.keys()) == {"scuz_patrol", "alfredokilgore"}
        assert results["scuz_patrol"]["handle"] == "scuz_patrol"

    def test_one_failure_does_not_block_others(self):
        def fake_fetch(handle):
            if handle == "broken_handle":
                raise Exception("404")
            return {"handle": handle}

        with patch("src.suno_client.fetch_profile", side_effect=fake_fetch):
            results = suno_client.fetch_profiles_parallel({"scuz_patrol", "broken_handle"})

        assert "scuz_patrol" in results
        assert "broken_handle" not in results


class TestFindFlaggedClips:
    """Test diffing live profile data against the cached manifest."""

    def test_flags_new_clip(self):
        profiles = {"scuz_patrol": {"clips": [{"id": "clip1", "title": "Song", "comment_count": 3}]}}
        manifest = {}

        flagged = suno_client.find_flagged_clips(profiles, manifest)

        assert len(flagged) == 1
        assert flagged[0]["clip_id"] == "clip1"

    def test_flags_clip_with_changed_comment_count(self):
        profiles = {"scuz_patrol": {"clips": [{"id": "clip1", "title": "Song", "comment_count": 5}]}}
        manifest = {"clip1": {"comment_count": 3}}

        flagged = suno_client.find_flagged_clips(profiles, manifest)

        assert len(flagged) == 1

    def test_does_not_flag_unchanged_clip(self):
        profiles = {"scuz_patrol": {"clips": [{"id": "clip1", "title": "Song", "comment_count": 3}]}}
        manifest = {"clip1": {"comment_count": 3}}

        flagged = suno_client.find_flagged_clips(profiles, manifest)

        assert flagged == []


class TestExtractNewCanonReplies:
    """Test lore-drop extraction from nested comment replies."""

    def test_extracts_reply_from_canon_handle(self):
        comments = {
            "results": [
                {
                    "id": "c1",
                    "content": "love this song",
                    "replies": [
                        {"id": "r1", "user_handle": "alfredokilgore", "content": "thanks, wrote it in 2020"}
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
                    "replies": [{"id": "r1", "user_handle": "randomfan", "content": "yeah"}],
                }
            ]
        }

        new_replies = suno_client.extract_new_canon_replies(comments, seen_ids=set())

        assert new_replies == []

    def test_ignores_already_seen_reply(self):
        comments = {
            "results": [
                {"id": "c1", "content": "cool", "replies": [{"id": "r1", "user_handle": "metrivus", "content": "yeah"}]}
            ]
        }

        new_replies = suno_client.extract_new_canon_replies(comments, seen_ids={"r1"})

        assert new_replies == []


class TestManifestStorage:
    """Test S3-backed manifest load/save."""

    def test_load_manifest_returns_parsed_json(self, monkeypatch):
        monkeypatch.setenv("MANIFEST_BUCKET", "test-bucket")
        mock_s3 = Mock()
        mock_body = Mock()
        mock_body.read.return_value = json.dumps({"clip1": {"comment_count": 2}}).encode()
        mock_s3.get_object.return_value = {"Body": mock_body}

        with patch("src.suno_client.boto3.client", return_value=mock_s3):
            manifest = suno_client.load_manifest()

        assert manifest == {"clip1": {"comment_count": 2}}
        mock_s3.get_object.assert_called_once_with(Bucket="test-bucket", Key="manifest.json")

    def test_load_manifest_returns_empty_dict_when_missing(self, monkeypatch):
        monkeypatch.setenv("MANIFEST_BUCKET", "test-bucket")
        mock_s3 = Mock()
        mock_s3.exceptions.NoSuchKey = FakeNoSuchKey
        mock_s3.get_object.side_effect = FakeNoSuchKey()

        with patch("src.suno_client.boto3.client", return_value=mock_s3):
            manifest = suno_client.load_manifest()

        assert manifest == {}

    def test_save_manifest_writes_json(self, monkeypatch):
        monkeypatch.setenv("MANIFEST_BUCKET", "test-bucket")
        mock_s3 = Mock()

        with patch("src.suno_client.boto3.client", return_value=mock_s3):
            suno_client.save_manifest({"clip1": {"comment_count": 2}})

        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Key"] == "manifest.json"
        assert json.loads(call_kwargs["Body"]) == {"clip1": {"comment_count": 2}}

    def test_raises_without_manifest_bucket(self, monkeypatch):
        monkeypatch.delenv("MANIFEST_BUCKET", raising=False)

        with pytest.raises(ValueError, match="MANIFEST_BUCKET"):
            suno_client.load_manifest()


class TestRefresh:
    """Test the end-to-end refresh orchestration."""

    def test_finds_new_lore_drop_and_updates_manifest(self, monkeypatch):
        monkeypatch.setenv("MANIFEST_BUCKET", "test-bucket")

        profiles = {
            "scuz_patrol": {
                "clips": [{"id": "clip1", "title": "Incarcerator", "comment_count": 2}]
            }
        }
        clip_data = {
            "clip1": {
                "clip": {
                    "title": "Incarcerator",
                    "handle": "scuz_patrol",
                    "created_at": "2026-01-01",
                    "comment_count": 2,
                },
                "comments": {
                    "results": [
                        {
                            "id": "c1",
                            "content": "nice",
                            "replies": [
                                {"id": "r1", "user_handle": "alfredokilgore", "content": "wrote this in prison"}
                            ],
                        }
                    ]
                },
            }
        }

        with patch("src.suno_client.load_manifest", return_value={}), \
             patch("src.suno_client.save_manifest") as mock_save, \
             patch("src.suno_client.fetch_profiles_parallel", return_value=profiles), \
             patch("src.suno_client.fetch_clip_data_parallel", return_value=clip_data):
            result = suno_client.refresh(handles={"scuz_patrol"})

        assert result["profiles_checked"] == 1
        assert result["clips_checked"] == 1
        assert len(result["new_lore_drops"]) == 1
        assert result["new_lore_drops"][0]["content"] == "wrote this in prison"
        assert result["new_lore_drops"][0]["handle"] == "alfredokilgore"

        saved_manifest = mock_save.call_args.args[0]
        assert saved_manifest["clip1"]["comment_count"] == 2
        assert "r1" in saved_manifest["clip1"]["cached_comment_ids"]

    def test_no_flagged_clips_means_no_lore_drops(self, monkeypatch):
        monkeypatch.setenv("MANIFEST_BUCKET", "test-bucket")

        profiles = {"scuz_patrol": {"clips": [{"id": "clip1", "title": "Song", "comment_count": 2}]}}
        manifest = {"clip1": {"comment_count": 2, "cached_comment_ids": []}}

        with patch("src.suno_client.load_manifest", return_value=manifest), \
             patch("src.suno_client.save_manifest") as mock_save, \
             patch("src.suno_client.fetch_profiles_parallel", return_value=profiles), \
             patch("src.suno_client.fetch_clip_data_parallel") as mock_fetch_clips:
            result = suno_client.refresh(handles={"scuz_patrol"})

        assert result["clips_checked"] == 0
        assert result["new_lore_drops"] == []
        mock_fetch_clips.assert_called_once_with([])
        mock_save.assert_called_once()
