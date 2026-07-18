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
    with patch("src.google_docs_client.build") as mock_build, \
         patch("src.google_docs_client.Credentials.from_service_account_info"):
        service = Mock()
        mock_build.return_value = service
        yield service


@pytest.fixture
def client(monkeypatch, mock_service):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_KEY", json.dumps({"type": "service_account"}))
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
        mock_service.documents().get().execute.return_value = {"body": {"content": content}}

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
        mock_service.documents().get().execute.return_value = {"body": {"content": content}}

        client.append_to_section("New band member info", "Band Members")

        batch_call = mock_service.documents().batchUpdate.call_args
        insert_request = batch_call.kwargs["body"]["requests"][0]["insertText"]
        assert insert_request["location"]["index"] == 46  # content[-1]['endIndex'] - 1

    def test_falls_back_to_end_of_document_when_section_missing(self, client, mock_service):
        """Should append to the end of the doc if no heading matches the section."""
        content = [
            heading("Band Chronology\n", 1, 18),
            body_paragraph("Some history.\n", 18, 32),
        ]
        mock_service.documents().get().execute.return_value = {"body": {"content": content}}

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
        mock_service.documents().get().execute.return_value = {"body": {"content": content}}

        client.append_to_section("New info", "  band members  ")

        batch_call = mock_service.documents().batchUpdate.call_args
        insert_request = batch_call.kwargs["body"]["requests"][0]["insertText"]
        assert insert_request["location"]["index"] == 15
