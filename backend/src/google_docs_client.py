"""Google Docs client for reading/writing the canon compendium."""

import base64
import json
import logging
import os
from typing import Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger()


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
            service_account_key = os.getenv('GOOGLE_SERVICE_ACCOUNT_KEY')

        if not service_account_key:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY not set")

        # Handle both base64-encoded and plain JSON formats
        key_data = self._parse_service_account_key(service_account_key)
        if not key_data:
            raise ValueError("Failed to parse service account key")

        # Create credentials scoped to Google Docs API
        self.credentials = Credentials.from_service_account_info(
            key_data,
            scopes=['https://www.googleapis.com/auth/documents']
        )

        self.service = build('docs', 'v1', credentials=self.credentials)
        self.doc_id = os.getenv('GOOGLE_DOC_ID')
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
            content = doc.get('body', {}).get('content', [])

            # Extract text from document structure
            text_parts = []
            for element in content:
                if 'paragraph' in element:
                    paragraph = element['paragraph']
                    para_text = self._extract_text_from_element(paragraph)
                    if para_text.strip():
                        text_parts.append(para_text)
                elif 'table' in element:
                    # Skip tables for now, just note they exist
                    text_parts.append("[TABLE]")

            result = '\n'.join(text_parts)
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

        if 'elements' in element:
            for run in element['elements']:
                if 'textRun' in run:
                    text += run['textRun'].get('content', '')

        return text

    def append_to_document(self, text: str) -> None:
        """Append text to the very end of the document.

        Args:
            text: The text to add
        """
        try:
            logger.info("Appending to end of document")

            requests = [
                {
                    'insertText': {
                        'text': f'\n\n{text}',
                        'endOfDocument': True
                    }
                }
            ]

            self.service.documents().batchUpdate(
                documentId=self.doc_id,
                body={'requests': requests}
            ).execute()

            logger.info(f"Successfully appended {len(text)} characters")

        except Exception as e:
            logger.error(f"Failed to append to document: {e}")
            raise

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
            content = doc.get('body', {}).get('content', [])

            insert_index = None
            in_target_section = False

            for element in content:
                paragraph = element.get('paragraph')
                if not paragraph:
                    continue

                style = paragraph.get('paragraphStyle', {}).get('namedStyleType', '')
                if not style.startswith('HEADING'):
                    continue

                if in_target_section:
                    # Found the next heading after our target section started
                    insert_index = element['startIndex']
                    break

                heading_text = self._extract_text_from_element(paragraph).strip()
                if heading_text.lower() == section.strip().lower():
                    in_target_section = True

            if in_target_section and insert_index is None and content:
                # Target section was the last one in the document
                insert_index = content[-1]['endIndex'] - 1

            if insert_index is None:
                logger.warning(f"Section '{section}' not found, appending to end of document")
                self.append_to_document(text)
                return

            logger.info(f"Inserting lore into section '{section}' at index {insert_index}")
            requests = [
                {
                    'insertText': {
                        'text': f'\n{text}\n',
                        'location': {'index': insert_index},
                    }
                }
            ]

            self.service.documents().batchUpdate(
                documentId=self.doc_id,
                body={'requests': requests}
            ).execute()

            logger.info(f"Successfully inserted {len(text)} characters into section '{section}'")

        except Exception as e:
            logger.error(f"Failed to append to section '{section}': {e}")
            raise
