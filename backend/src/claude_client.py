"""Claude AI client for lore classification and Q&A."""

import json
import logging
import os
from typing import Any, Dict, Optional

import anthropic

logger = logging.getLogger()


class ClaudeClient:
    """Interface to Claude API with prompt caching for cost optimization."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Claude client.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
        """
        self.api_key = api_key or os.getenv('ANTHROPIC_API_KEY')
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = "claude-3-5-sonnet-20241022"

    def classify_intent(
        self,
        user_message: str,
        canon_doc: str,
    ) -> Dict[str, Any]:
        """Classify user message as lore/question/neither.

        Uses the full canon doc with prompt caching to minimize token costs.

        Args:
            user_message: The user's input
            canon_doc: Full canon compendium (markdown text)

        Returns:
            {
                "intent": "new_lore" | "question" | "neither",
                "confidence": 0.0-1.0,
                "suggested_section": str (for new_lore),
                "reasoning": str,
            }
        """
        system_prompt = f"""You are a curator for the Scuz Patrol fictional band canon.

The canon compendium is provided below. Your job is to classify incoming messages from Discord.

Classify each message as one of:
1. **new_lore**: New information about the band that should be added to the canon
2. **question**: A question about existing lore that needs to be answered
3. **neither**: Not relevant to the lore (off-topic chat, images, etc.)

For new_lore, suggest which section it belongs in (Band Chronology, Band Members, Supporting Characters, Virtual Discography, etc.).

For questions, identify what part of the canon is relevant.

Respond as JSON only, no other text.

CANON COMPENDIUM:
{canon_doc}"""

        user_prompt = f"""Classify this message:

{user_message}

Respond with JSON matching this schema:
{{
  "intent": "new_lore" | "question" | "neither",
  "confidence": 0.0-1.0,
  "suggested_section": "section name or null",
  "reasoning": "brief explanation"
}}"""

        try:
            logger.info(f"Classifying message: {user_message[:100]}...")

            response = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": user_prompt,
                    }
                ],
            )

            # Parse the JSON response
            response_text = response.content[0].text.strip()
            try:
                result = json.loads(response_text)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse Claude response as JSON: {response_text}")
                return {
                    "intent": "neither",
                    "confidence": 0.0,
                    "reasoning": "Claude response format error",
                }

            # Log cache usage for cost tracking
            usage = response.usage
            logger.info(
                f"Claude usage: input={usage.input_tokens}, "
                f"output={usage.output_tokens}, "
                f"cache_creation={getattr(usage, 'cache_creation_input_tokens', 0)}, "
                f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)}"
            )

            return result

        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            raise

    def answer_question(
        self,
        question: str,
        canon_doc: str,
    ) -> str:
        """Answer a lore question using the canon doc.

        Args:
            question: The user's question
            canon_doc: Full canon compendium

        Returns:
            The answer with citations
        """
        system_prompt = f"""You are a helpful guide to the Scuz Patrol fictional band lore.

Use the canon compendium below to answer questions about the band, its members, storylines, and discography.

When referencing lore, cite the specific section or song you're referencing. Be concise and accurate.

CANON COMPENDIUM:
{canon_doc}"""

        try:
            logger.info(f"Answering question: {question[:100]}...")

            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": question,
                    }
                ],
            )

            answer = response.content[0].text

            # Log cache usage
            usage = response.usage
            logger.info(
                f"Claude usage: input={usage.input_tokens}, "
                f"output={usage.output_tokens}, "
                f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)}"
            )

            return answer

        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            raise
