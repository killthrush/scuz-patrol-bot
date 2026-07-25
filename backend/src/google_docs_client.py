"""Google Docs client for reading/writing the canon compendium."""

import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger()

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _convert_markdown(
    text: str,
) -> Tuple[str, List[Tuple[int, int]], List[Tuple[int, int]]]:
    """Strip the limited markdown the synthesis prompt asks for (**bold**
    headers, "- " bullets) and return plain text alongside the character
    ranges that need real Google Docs formatting instead.

    Docs has no markdown renderer -- inserting the raw markers as text just
    puts literal asterisks and dashes in the document. Returns
    (clean_text, bold_ranges, bullet_paragraph_ranges), with every range an
    offset pair into clean_text.
    """
    raw_lines = text.split("\n")
    bullet_flags = []
    delisted_lines = []
    for line in raw_lines:
        stripped = line.lstrip()
        if stripped.startswith("- "):
            indent = len(line) - len(stripped)
            delisted_lines.append(line[:indent] + stripped[2:])
            bullet_flags.append(True)
        else:
            delisted_lines.append(line)
            bullet_flags.append(False)

    intermediate = "\n".join(delisted_lines)

    clean_parts = []
    bold_ranges = []
    pos = 0
    out_len = 0
    for match in _BOLD_RE.finditer(intermediate):
        clean_parts.append(intermediate[pos : match.start()])
        out_len += match.start() - pos
        bold_text = match.group(1)
        clean_parts.append(bold_text)
        bold_ranges.append((out_len, out_len + len(bold_text)))
        out_len += len(bold_text)
        pos = match.end()
    clean_parts.append(intermediate[pos:])
    clean_text = "".join(clean_parts)

    bullet_ranges = []
    line_start = 0
    for line, is_bullet in zip(clean_text.split("\n"), bullet_flags):
        if is_bullet and line.strip():
            bullet_ranges.append((line_start, line_start + len(line)))
        line_start += len(line) + 1

    return clean_text, bold_ranges, bullet_ranges


class GoogleDocsClient:
    """Interface to Google Docs API for the canon compendium."""

    def __init__(self, service_account_key: Optional[str] = None):
        """Initialize Google Docs client.

        Args:
            service_account_key: Service account JSON (string or base64)
                (defaults to GOOGLE_SERVICE_ACCOUNT_KEY env var,
                 populated by handler from Secrets Manager)
        """
        if service_account_key is None:
            service_account_key = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")

        if not service_account_key:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY not set")

        # Handle both base64-encoded and plain JSON formats
        key_data = self._parse_service_account_key(service_account_key)
        if not key_data:
            raise ValueError("Failed to parse service account key")

        # Create credentials scoped to Google Docs API
        self.credentials = Credentials.from_service_account_info(
            key_data, scopes=["https://www.googleapis.com/auth/documents"]
        )

        self.service = build("docs", "v1", credentials=self.credentials)
        self.doc_id = os.getenv("GOOGLE_DOC_ID")
        if not self.doc_id:
            raise ValueError("GOOGLE_DOC_ID not set")

    def _parse_service_account_key(self, key: str) -> Optional[dict]:
        """Parse service account key from base64 or plain JSON.

        Args:
            key: Base64-encoded or plain JSON service account key

        Returns:
            Parsed key dict or None if parsing fails
        """
        # Try parsing as plain JSON first (from Secrets Manager)
        try:
            return json.loads(key)
        except json.JSONDecodeError:
            pass

        # Try base64 decoding (from env var)
        try:
            key_json = base64.b64decode(key).decode()
            return json.loads(key_json)
        except Exception:
            logger.error("Failed to parse service account key as JSON or base64")
            return None

    def read_document(self) -> str:
        """Read the full canon compendium as plain text.

        Returns:
            The document content as markdown/plain text
        """
        try:
            logger.info(f"Reading document {self.doc_id}")

            doc = self.service.documents().get(documentId=self.doc_id).execute()
            content = doc.get("body", {}).get("content", [])

            # Extract text from document structure
            text_parts = []
            for element in content:
                if "paragraph" in element:
                    paragraph = element["paragraph"]
                    para_text = self._extract_text_from_element(paragraph)
                    if para_text.strip():
                        text_parts.append(para_text)
                elif "table" in element:
                    # Skip tables for now, just note they exist
                    text_parts.append("[TABLE]")

            result = "\n".join(text_parts)
            logger.info(f"Read {len(result)} characters from canon doc")
            return result

        except Exception as e:
            logger.error(f"Failed to read document: {e}")
            raise

    def _extract_text_from_element(self, element: dict) -> str:
        """Extract text content from a document element (paragraph, etc).

        Args:
            element: A document element (paragraph, text run, etc.)

        Returns:
            Plain text content
        """
        text = ""

        if "elements" in element:
            for run in element["elements"]:
                if "textRun" in run:
                    text += run["textRun"].get("content", "")

        return text

    def append_to_document(self, text: str) -> None:
        """Append text to the very end of the document.

        Args:
            text: The text to add
        """
        try:
            logger.info("Appending to end of document")

            requests = [{"insertText": {"text": f"\n\n{text}", "endOfDocument": True}}]

            self.service.documents().batchUpdate(
                documentId=self.doc_id, body={"requests": requests}
            ).execute()

            logger.info(f"Successfully appended {len(text)} characters")

        except Exception as e:
            logger.error(f"Failed to append to document: {e}")
            raise

    def _heading_text(self, paragraph: Dict[str, Any]) -> Optional[str]:
        """Return a paragraph's heading text, or None if it isn't a real heading.

        A heading-styled paragraph with no actual text isn't a real section
        boundary -- inserting text right at a heading's edge can leave
        behind an empty paragraph that inherits the adjacent heading's style
        (namedStyleType HEADING_2 etc.) despite having no heading content.
        Treating that phantom as a real heading corrupts every boundary
        calculation downstream of it (e.g. computing a zero-length section
        range, silently turning a "replace this section" into "prepend to
        it forever").
        """
        style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
        if not style.startswith("HEADING"):
            return None
        text = self._extract_text_from_element(paragraph).strip()
        return text or None

    def _formatting_requests(
        self,
        base_index: int,
        bold_ranges: List[Tuple[int, int]],
        bullet_ranges: List[Tuple[int, int]],
    ) -> List[Dict[str, Any]]:
        """Build updateTextStyle/createParagraphBullets requests for ranges
        already inserted at base_index, given as offsets into that text."""
        requests: List[Dict[str, Any]] = []
        for start, end in bold_ranges:
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {
                            "startIndex": base_index + start,
                            "endIndex": base_index + end,
                        },
                        "textStyle": {"bold": True},
                        "fields": "bold",
                    }
                }
            )
        for start, end in bullet_ranges:
            requests.append(
                {
                    "createParagraphBullets": {
                        "range": {
                            "startIndex": base_index + start,
                            "endIndex": base_index + end,
                        },
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                }
            )
        return requests

    def append_to_section(self, text: str, section: str) -> None:
        """Insert new lore text at the end of a named section.

        Finds a heading paragraph matching `section` (case-insensitive) and
        inserts the text just before the next heading, i.e. at the end of
        that section's existing content. Falls back to appending at the end
        of the document if no matching heading is found.

        Args:
            text: The lore text to add
            section: The section heading to insert under (e.g., "Band Members")
        """
        try:
            doc = self.service.documents().get(documentId=self.doc_id).execute()
            content = doc.get("body", {}).get("content", [])

            insert_index = None
            in_target_section = False

            for element in content:
                paragraph = element.get("paragraph")
                if not paragraph:
                    continue

                heading_text = self._heading_text(paragraph)
                if heading_text is None:
                    continue

                if in_target_section:
                    # Found the next heading after our target section started
                    insert_index = element["startIndex"]
                    break

                if heading_text.lower() == section.strip().lower():
                    in_target_section = True

            if in_target_section and insert_index is None and content:
                # Target section was the last one in the document
                insert_index = content[-1]["endIndex"] - 1

            if insert_index is None:
                logger.warning(
                    f"Section '{section}' not found, appending to end of document"
                )
                self.append_to_document(text)
                return

            logger.info(
                f"Inserting lore into section '{section}' at index {insert_index}"
            )
            clean_text, bold_ranges, bullet_ranges = _convert_markdown(text)
            requests = [
                {
                    "insertText": {
                        "text": f"\n{clean_text}\n",
                        "location": {"index": insert_index},
                    }
                }
            ]
            requests.extend(
                self._formatting_requests(insert_index + 1, bold_ranges, bullet_ranges)
            )

            self.service.documents().batchUpdate(
                documentId=self.doc_id, body={"requests": requests}
            ).execute()

            logger.info(
                f"Successfully inserted {len(text)} characters into section '{section}'"
            )

        except Exception as e:
            logger.error(f"Failed to append to section '{section}': {e}")
            raise

    def _find_section_range(
        self, content: List[Dict[str, Any]], section: str
    ) -> Optional[Tuple[int, int]]:
        """Find (start_index, end_index) spanning a section's body -- from
        right after its heading through right before the next heading (or
        the end of the document if it's the last section).

        Returns None if no heading matches `section` (case-insensitive).
        """
        start_index = None
        end_index = None
        in_target_section = False

        for element in content:
            paragraph = element.get("paragraph")
            if not paragraph:
                continue

            heading_text = self._heading_text(paragraph)
            if heading_text is None:
                continue

            if in_target_section:
                end_index = element["startIndex"]
                break

            if heading_text.lower() == section.strip().lower():
                in_target_section = True
                start_index = element["endIndex"]

        if start_index is None:
            return None
        if end_index is None:
            # Target section was the last one in the document.
            end_index = content[-1]["endIndex"] - 1

        return start_index, end_index

    def replace_section_content(self, section: str, new_content: str) -> None:
        """Replace a section's entire body with new_content.

        Unlike append_to_section, this deletes the section's existing body
        (everything from right after its heading through right before the
        next heading) before inserting -- used by doc reconstruction, which
        synthesizes a section's complete new text from its full fact
        history rather than tacking one fact onto the end.

        Re-reads the document fresh on every call rather than relying on
        indices computed earlier -- safe to call repeatedly for different
        sections in one reconstruction pass without needing to reason about
        how an earlier section's edit shifted later indices.

        Raises if the section heading isn't found -- unlike append_to_section,
        there's no safe fallback here (appending elsewhere would just create
        a duplicate of content the caller intended to replace).
        """
        try:
            doc = self.service.documents().get(documentId=self.doc_id).execute()
            content = doc.get("body", {}).get("content", [])

            section_range = self._find_section_range(content, section)
            if section_range is None:
                raise ValueError(f"Section '{section}' not found")
            start_index, end_index = section_range

            clean_text, bold_ranges, bullet_ranges = _convert_markdown(new_content)

            requests: List[Dict[str, Any]] = []
            if end_index > start_index:
                requests.append(
                    {
                        "deleteContentRange": {
                            "range": {
                                "startIndex": start_index,
                                "endIndex": end_index,
                            }
                        }
                    }
                )
            requests.append(
                {
                    "insertText": {
                        "text": f"\n{clean_text}\n",
                        "location": {"index": start_index},
                    }
                }
            )
            requests.extend(
                self._formatting_requests(start_index + 1, bold_ranges, bullet_ranges)
            )

            self.service.documents().batchUpdate(
                documentId=self.doc_id, body={"requests": requests}
            ).execute()

            logger.info(
                f"Replaced section '{section}' with {len(new_content)} characters"
            )

        except Exception as e:
            logger.error(f"Failed to replace section '{section}': {e}")
            raise
