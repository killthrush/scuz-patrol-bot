"""DynamoDB-backed fact store: the durable ledger of atomic lore facts the
canon doc gets periodically reconstructed from.

Facts are append-only. A retcon supersedes an old fact rather than
overwriting or deleting it, so the audit trail -- and the ability to recover
from a bad doc rewrite -- never depends on data that's already been
destroyed. The canon doc itself is treated as a disposable, regeneratable
projection of whatever facts are currently non-superseded.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

logger = logging.getLogger()

MAX_FACT_LENGTH = 2000

STATUS_PENDING = "pending"
STATUS_INTEGRATED = "integrated"
STATUS_SUPERSEDED = "superseded"

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def _table_name() -> str:
    table_name = os.getenv("FACTS_TABLE")
    if not table_name:
        raise ValueError("FACTS_TABLE not set")
    return table_name


def _to_item(fact: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _serializer.serialize(v) for k, v in fact.items()}


def _from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _deserializer.deserialize(v) for k, v in item.items()}


def put_fact(
    content: str,
    section_hint: str,
    handle: str,
    source: str,
    source_ref: str,
    classification: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a new fact to the store.

    Args:
        content: The lore text itself. Capped at MAX_FACT_LENGTH so a single
            fact can't blow out the doc-reconstruction prompt later.
        section_hint: Suggested canon doc section (from Claude's classification).
        handle: Who said it -- a Discord username or a Suno account handle.
        source: Where it came from, e.g. "discord_lore" or "suno_reply".
        source_ref: An ID tying the fact back to its origin (Discord message/
            interaction ID, Suno reply ID) for audit/traceability.
        classification: Raw intent classification, kept for audit.

    Raises:
        ValueError: If content exceeds MAX_FACT_LENGTH characters.
    """
    if len(content) > MAX_FACT_LENGTH:
        raise ValueError(
            f"Fact content exceeds {MAX_FACT_LENGTH} characters ({len(content)})"
        )

    fact = {
        "fact_id": str(uuid.uuid4()),
        "content": content,
        "section_hint": section_hint,
        "handle": handle,
        "source": source,
        "source_ref": source_ref,
        "classification": classification or "",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS_PENDING,
    }

    client = boto3.client("dynamodb")
    client.put_item(TableName=_table_name(), Item=_to_item(fact))
    return fact


def get_pending_facts() -> List[Dict[str, Any]]:
    """Return all facts not yet integrated into the canon doc, oldest first."""
    client = boto3.client("dynamodb")
    response = client.query(
        TableName=_table_name(),
        IndexName="status-ingested_at-index",
        KeyConditionExpression="#status = :status",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": {"S": STATUS_PENDING}},
        ScanIndexForward=True,
    )
    return [_from_item(item) for item in response.get("Items", [])]


def mark_integrated(fact_id: str, doc_version: str) -> None:
    """Mark a fact as folded into a specific canon doc rewrite."""
    client = boto3.client("dynamodb")
    client.update_item(
        TableName=_table_name(),
        Key={"fact_id": {"S": fact_id}},
        UpdateExpression="SET #status = :status, integrated_doc_version = :v",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": {"S": STATUS_INTEGRATED},
            ":v": {"S": doc_version},
        },
    )


def supersede_fact(old_fact_id: str, new_fact_id: str) -> None:
    """Mark an old fact as superseded by a newer, contradicting one.

    The old fact is kept (not deleted) for audit history, but excluded from
    future doc reconstructions in favor of the fact that superseded it.
    """
    client = boto3.client("dynamodb")
    client.update_item(
        TableName=_table_name(),
        Key={"fact_id": {"S": old_fact_id}},
        UpdateExpression="SET #status = :status, superseded_by = :new_id",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":status": {"S": STATUS_SUPERSEDED},
            ":new_id": {"S": new_fact_id},
        },
    )
