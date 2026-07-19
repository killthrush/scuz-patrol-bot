"""Unit tests for the DynamoDB-backed fact store (mocked boto3)."""

from unittest.mock import Mock, patch

import pytest

from src import fact_store


@pytest.fixture
def mock_dynamo_client(monkeypatch):
    monkeypatch.setenv("FACTS_TABLE", "test-facts-table")
    with patch("src.fact_store.boto3.client") as mock_client_factory:
        mock_client = Mock()
        mock_client_factory.return_value = mock_client
        yield mock_client


class TestPutFact:
    """Test writing a new fact to the store."""

    def test_writes_fact_with_generated_id(self, mock_dynamo_client):
        fact = fact_store.put_fact(
            content="Kilgore joined in 2020",
            section_hint="Band Members",
            handle="killthrush",
            source="discord_lore",
            source_ref="msg123",
        )

        assert fact["content"] == "Kilgore joined in 2020"
        assert fact["section_hint"] == "Band Members"
        assert fact["handle"] == "killthrush"
        assert fact["source"] == "discord_lore"
        assert fact["source_ref"] == "msg123"
        assert fact["status"] == fact_store.STATUS_PENDING
        assert fact["fact_id"]
        assert fact["ingested_at"]

        mock_dynamo_client.put_item.assert_called_once()
        call_kwargs = mock_dynamo_client.put_item.call_args.kwargs
        assert call_kwargs["TableName"] == "test-facts-table"
        assert call_kwargs["Item"]["content"]["S"] == "Kilgore joined in 2020"
        assert call_kwargs["Item"]["status"]["S"] == "pending"

    def test_rejects_content_over_max_length(self, mock_dynamo_client):
        too_long = "x" * (fact_store.MAX_FACT_LENGTH + 1)

        with pytest.raises(ValueError, match="2000"):
            fact_store.put_fact(
                content=too_long,
                section_hint="Band Members",
                handle="killthrush",
                source="discord_lore",
                source_ref="msg123",
            )

        assert not mock_dynamo_client.put_item.called

    def test_accepts_content_at_exactly_max_length(self, mock_dynamo_client):
        exactly_max = "x" * fact_store.MAX_FACT_LENGTH

        fact = fact_store.put_fact(
            content=exactly_max,
            section_hint="Band Members",
            handle="killthrush",
            source="discord_lore",
            source_ref="msg123",
        )

        assert fact["content"] == exactly_max
        assert mock_dynamo_client.put_item.called

    def test_raises_without_facts_table(self, monkeypatch):
        monkeypatch.delenv("FACTS_TABLE", raising=False)

        with pytest.raises(ValueError, match="FACTS_TABLE"):
            fact_store.put_fact(
                content="Kilgore joined in 2020",
                section_hint="Band Members",
                handle="killthrush",
                source="discord_lore",
                source_ref="msg123",
            )


class TestGetPendingFacts:
    """Test querying facts not yet integrated into the canon doc."""

    def test_returns_deserialized_pending_facts(self, mock_dynamo_client):
        mock_dynamo_client.query.return_value = {
            "Items": [
                {
                    "fact_id": {"S": "f1"},
                    "content": {"S": "Kilgore joined in 2020"},
                    "status": {"S": "pending"},
                    "ingested_at": {"S": "2026-01-01T00:00:00+00:00"},
                }
            ]
        }

        facts = fact_store.get_pending_facts()

        assert len(facts) == 1
        assert facts[0]["fact_id"] == "f1"
        assert facts[0]["content"] == "Kilgore joined in 2020"

        call_kwargs = mock_dynamo_client.query.call_args.kwargs
        assert call_kwargs["TableName"] == "test-facts-table"
        assert call_kwargs["IndexName"] == "status-ingested_at-index"
        assert call_kwargs["ExpressionAttributeValues"][":status"] == {"S": "pending"}
        assert call_kwargs["ScanIndexForward"] is True

    def test_returns_empty_list_when_no_pending_facts(self, mock_dynamo_client):
        mock_dynamo_client.query.return_value = {"Items": []}

        facts = fact_store.get_pending_facts()

        assert facts == []


class TestMarkIntegrated:
    """Test marking a fact as folded into a canon doc rewrite."""

    def test_updates_status_and_doc_version(self, mock_dynamo_client):
        fact_store.mark_integrated("f1", "doc-v3")

        call_kwargs = mock_dynamo_client.update_item.call_args.kwargs
        assert call_kwargs["TableName"] == "test-facts-table"
        assert call_kwargs["Key"] == {"fact_id": {"S": "f1"}}
        assert call_kwargs["ExpressionAttributeValues"][":status"] == {
            "S": "integrated"
        }
        assert call_kwargs["ExpressionAttributeValues"][":v"] == {"S": "doc-v3"}


class TestSupersedeFact:
    """Test marking an old fact as superseded by a newer one, without deleting it."""

    def test_updates_status_and_superseded_by(self, mock_dynamo_client):
        fact_store.supersede_fact("old_fact", "new_fact")

        call_kwargs = mock_dynamo_client.update_item.call_args.kwargs
        assert call_kwargs["TableName"] == "test-facts-table"
        assert call_kwargs["Key"] == {"fact_id": {"S": "old_fact"}}
        assert call_kwargs["ExpressionAttributeValues"][":status"] == {
            "S": "superseded"
        }
        assert call_kwargs["ExpressionAttributeValues"][":new_id"] == {"S": "new_fact"}
