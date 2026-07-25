"""Unit tests for Google Docs client (mocked)."""

import json
import pytest
from unittest.mock import Mock, patch
from src.google_docs_client import GoogleDocsClient, _convert_markdown


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


def phantom_heading(start: int, end: int) -> dict:
    """Build a mock empty paragraph that carries a HEADING style with no
    text -- can happen when insertText at a heading's edge causes the new
    paragraph break to inherit the adjacent heading's style. Must never be
    treated as a real section boundary."""
    return {
        "startIndex": start,
        "endIndex": end,
        "paragraph": {
            "elements": [{"textRun": {"content": "\n"}}],
            "paragraphStyle": {"namedStyleType": "HEADING_2"},
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

    def test_phantom_empty_heading_styled_paragraph_is_not_a_boundary(
        self, client, mock_service
    ):
        """A stray empty paragraph that inherited a HEADING style (e.g. left
        behind by a previous insert) must not be mistaken for the section's
        end -- otherwise real content past it gets skipped over."""
        content = [
            heading("Band Chronology\n", 1, 18),
            phantom_heading(18, 19),
            body_paragraph("Real existing content.\n", 19, 43),
            heading("Open Threads\n", 43, 57),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.append_to_section("New lore", "Band Chronology")

        batch_call = mock_service.documents().batchUpdate.call_args
        insert_request = batch_call.kwargs["body"]["requests"][0]["insertText"]
        assert insert_request["location"]["index"] == 43


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

    def test_phantom_empty_heading_styled_paragraph_is_not_a_boundary(
        self, client, mock_service
    ):
        """Regression test for a real corruption: a stray empty paragraph
        that inherited a HEADING style (left behind by a previous
        insertText at a heading's edge) was being treated as the section's
        end, computing a zero-length range. The delete got skipped (since
        end == start), so every subsequent "replace" actually just prepended
        new content in front of the old body forever instead of replacing it.
        """
        content = [
            heading("Band Chronology\n", 1, 18),
            phantom_heading(18, 19),
            body_paragraph("Old content that must be deleted.\n", 19, 54),
            heading("Open Threads\n", 54, 68),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.replace_section_content("Band Chronology", "New complete body")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        assert requests[0]["deleteContentRange"]["range"] == {
            "startIndex": 18,
            "endIndex": 54,
        }
        assert requests[1]["insertText"]["location"]["index"] == 18

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

    def test_bold_markers_become_real_bold_formatting_not_literal_asterisks(
        self, client, mock_service
    ):
        """Docs has no markdown renderer -- **bold** markers in synthesized
        content must become updateTextStyle requests, not literal asterisks
        left sitting in the inserted text."""
        content = [
            heading("Band Members\n", 1, 15),
            heading("Supporting Characters\n", 15, 38),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.replace_section_content("Band Members", "**Alfredo Kilgore**\nBio text.")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        insert_text = requests[0]["insertText"]["text"]
        assert "*" not in insert_text
        assert "Alfredo Kilgore" in insert_text

        bold_requests = [
            r["updateTextStyle"] for r in requests if "updateTextStyle" in r
        ]
        assert len(bold_requests) == 1
        assert bold_requests[0]["textStyle"] == {"bold": True}
        assert bold_requests[0]["fields"] == "bold"
        # "\n" prefix (index 15 -> 16) then "Alfredo Kilgore" starts at 16
        assert bold_requests[0]["range"] == {"startIndex": 16, "endIndex": 31}

    def test_bullet_lines_become_real_bullets_not_literal_dashes(
        self, client, mock_service
    ):
        content = [
            heading("Unexplored Ideas\n", 1, 20),
        ]
        mock_service.documents().get().execute.return_value = {
            "body": {"content": content}
        }

        client.replace_section_content("Unexplored Ideas", "- Some speculative idea")

        batch_call = mock_service.documents().batchUpdate.call_args
        requests = batch_call.kwargs["body"]["requests"]
        insert_text = requests[0]["insertText"]["text"]
        assert "- " not in insert_text
        assert "Some speculative idea" in insert_text

        bullet_requests = [
            r["createParagraphBullets"]
            for r in requests
            if "createParagraphBullets" in r
        ]
        assert len(bullet_requests) == 1
        assert bullet_requests[0]["range"] == {"startIndex": 21, "endIndex": 42}


class TestConvertMarkdown:
    """Test the markdown-to-Docs-formatting-range converter in isolation."""

    def test_plain_text_is_unchanged_with_no_ranges(self):
        clean, bold_ranges, bullet_ranges = _convert_markdown("Just plain text.")
        assert clean == "Just plain text."
        assert bold_ranges == []
        assert bullet_ranges == []

    def test_strips_bold_markers_and_records_range(self):
        clean, bold_ranges, bullet_ranges = _convert_markdown("**Title.** Then prose.")
        assert clean == "Title. Then prose."
        assert bold_ranges == [(0, 6)]
        assert bullet_ranges == []

    def test_multiple_bold_spans_on_separate_lines(self):
        clean, bold_ranges, bullet_ranges = _convert_markdown(
            "**One**\nmiddle\n**Two**"
        )
        assert clean == "One\nmiddle\nTwo"
        assert bold_ranges == [(0, 3), (11, 14)]

    def test_strips_bullet_prefix_and_records_line_range(self):
        clean, bold_ranges, bullet_ranges = _convert_markdown(
            "Intro paragraph.\n- First idea\n- Second idea"
        )
        assert clean == "Intro paragraph.\nFirst idea\nSecond idea"
        assert bullet_ranges == [(17, 27), (28, 39)]

    def test_bold_bullet_line_gets_both(self):
        clean, bold_ranges, bullet_ranges = _convert_markdown("- **2055** happened")
        assert clean == "2055 happened"
        assert bold_ranges == [(0, 4)]
        assert bullet_ranges == [(0, 13)]

    def test_blank_bullet_line_is_not_treated_as_a_bullet_paragraph(self):
        clean, bold_ranges, bullet_ranges = _convert_markdown("- \nreal text")
        assert bullet_ranges == []
