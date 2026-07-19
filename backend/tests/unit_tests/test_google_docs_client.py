"""Unit tests for Google Docs client (mocked)."""

import json
import pytest
from unittest.mock import Mock, patch
from src.google_docs_client import GoogleDocsClient


def heading(text: str, start: int, end: int) -> dict:
    """Build a mock HEADING_1 paragraph element."""
    return {
        "startIndex": start,
        "endIndex": end,
        "paragraph": {
            "elements": [{"textRun": {"content": text}}],
            "paragraphStyle": {"namedStyleType": "HEADING_1"},
        },
    }


def body_paragraph(text: str, start: int, end: int) -> dict:
    """Build a mock plain (non-heading) paragraph element."""
    return {
        "startIndex": start,
        "endIndex": end,
        "paragraph": {
            "elements": [{"textRun": {"content": text}}],
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
        },
    }


@pytest.fixture
def mock_service():
    """Mock the Google Docs API service."""
    with patch("src.google_docs_client.build") as mock_build, patch(
        "src.google_docs_client.Credentials.from_service_account_info"
    ):
        service = Mock()
        mock_build.return_value = service
        yield service


@pytest.fixture
def client(monkeypatch, mock_service):
    monkeypatch.setenv(
        "GOOGLE_SERVICE_ACCOUNT_KEY", json.dumps({"type": "service_account"})
    )
    monkeypatch.setenv("GOOGLE_DOC_ID", "test-doc-id")
    return GoogleDocsClient()


class TestAppendToSection:
    """Test section-aware lore insertion."""

    def test_inserts_before_next_heading(self, client, mock_service):
        """Should insert right before the heading that follows the target section."""
        content = [
            heading("Band Chronology\n", 1, 18),
            body_paragraph("Some history.\n", 18, 32),
            heading("Band Members\n", 32, 46),
            body_paragraph("Kilgore, Kero.\n", 46, 61),
            heading("Supporting Characters\n", 61, 84),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.append_to_section("New band member info", "Band Members")

        batch_call = mock_service.documents().batchUpdate.call_args
        request_body = batch_call.kwargs["body"]
        insert_request = request_body["requests"][0]["insertText"]
        assert insert_request["location"]["index"] == 61
        assert "New band member info" in insert_request["text"]

    def test_inserts_at_end_when_target_is_last_section(self, client, mock_service):
        """Should insert at end of doc content when the section has no following heading."""
        content = [
            heading("Band Chronology\n", 1, 18),
            heading("Band Members\n", 18, 32),
            body_paragraph("Kilgore, Kero.\n", 32, 47),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.append_to_section("New band member info", "Band Members")

        batch_call = mock_service.documents().batchUpdate.call_args
        insert_request = batch_call.kwargs["body"]["requests"][0]["insertText"]
        assert insert_request["location"]["index"] == 46  # content[-1]['endIndex'] - 1

    def test_falls_back_to_end_of_document_when_section_missing(
        self, client, mock_service
    ):
        """Should append to the end of the doc if no heading matches the section."""
        content = [
            heading("Band Chronology\n", 1, 18),
            body_paragraph("Some history.\n", 18, 32),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.append_to_section("Orphan lore", "Nonexistent Section")

        batch_call = mock_service.documents().batchUpdate.call_args
        insert_request = batch_call.kwargs["body"]["requests"][0]["insertText"]
        assert insert_request.get("endOfDocument") is True
        assert "Orphan lore" in insert_request["text"]

    def test_matches_section_case_insensitively(self, client, mock_service):
        """Section matching should ignore case and surrounding whitespace."""
        content = [
            heading("Band Members\n", 1, 15),
            heading("Supporting Characters\n", 15, 38),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.append_to_section("New info", "  band members  ")

        batch_call = mock_service.documents().batchUpdate.call_args
        insert_request = batch_call.kwargs["body"]["requests"][0]["insertText"]
        assert insert_request["location"]["index"] == 15


class TestReplaceSectionContent:
    """Test wholesale section-body replacement used by doc reconstruction."""

    def test_deletes_existing_body_and_inserts_new_content(self, client, mock_service):
        content = [
            heading("Band Chronology\n", 1, 18),
            heading("Band Members\n", 18, 32),
            body_paragraph("Kilgore, Kero.\n", 32, 47),
            heading("Supporting Characters\n", 47, 70),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.replace_section_content("Band Members", "New complete body")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        assert requests[0]["deleteContentRange"]["range"] == {
            "startIndex": 32,
            "endIndex": 47,
        }
        assert requests[1]["insertText"]["location"]["index"] == 32
        assert "New complete body" in requests[1]["insertText"]["text"]

    def test_replaces_last_section_up_to_end_of_document(self, client, mock_service):
        content = [
            heading("Band Chronology\n", 1, 18),
            heading("Band Members\n", 18, 32),
            body_paragraph("Kilgore, Kero.\n", 32, 47),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.replace_section_content("Band Members", "New complete body")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        assert requests[0]["deleteContentRange"]["range"] == {
            "startIndex": 32,
            "endIndex": 46,  # content[-1]['endIndex'] - 1
        }

    def test_skips_delete_when_section_body_is_already_empty(
        self, client, mock_service
    ):
        content = [
            heading("Band Members\n", 1, 15),
            heading("Supporting Characters\n", 15, 38),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.replace_section_content("Band Members", "First content ever")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        assert len(requests) == 1
        assert "insertText" in requests[0]
        assert requests[0]["insertText"]["location"]["index"] == 15

    def test_raises_when_section_not_found(self, client, mock_service):
        content = [heading("Band Chronology\n", 1, 18)]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        with pytest.raises(ValueError, match="Nonexistent Section"):
            client.replace_section_content("Nonexistent Section", "New content")

        assert not mock_service.documents().batchUpdate.called

    def test_matches_section_case_insensitively(self, client, mock_service):
        content = [
            heading("Band Members\n", 1, 15),
            body_paragraph("Old content.\n", 15, 29),
            heading("Supporting Characters\n", 29, 52),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.replace_section_content("  band members  ", "New content")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        assert requests[0]["deleteContentRange"]["range"] == {
            "startIndex": 15,
            "endIndex": 29,
        }
