"""Unit tests for the EventBridge Scheduler-based reconstruction debounce."""

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from src import reconstruction_trigger


class FakeResourceNotFoundException(Exception):
    """Stand-in for boto3's scheduler.exceptions.ResourceNotFoundException."""

    pass


@pytest.fixture
def mock_scheduler_client(monkeypatch):
    monkeypatch.setenv("RECONSTRUCT_SCHEDULE_NAME", "test-reconstruct-debounce")
    monkeypatch.setenv(
        "RECONSTRUCT_LAMBDA_ARN", "arn:aws:lambda:us-east-1:123:function:reconstruct"
    )
    monkeypatch.setenv(
        "RECONSTRUCT_SCHEDULER_ROLE_ARN", "arn:aws:iam::123:role/scheduler-role"
    )
    with patch("src.reconstruction_trigger.boto3.client") as mock_client_factory:
        mock_client = Mock()
        mock_client.exceptions.ResourceNotFoundException = FakeResourceNotFoundException
        mock_client_factory.return_value = mock_client
        yield mock_client


class TestScheduleReconstruction:
    """Test debounced (re)scheduling of the one-time reconstruction trigger."""

    def test_updates_existing_schedule_with_pushed_back_fire_time(
        self, mock_scheduler_client
    ):
        now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

        reconstruction_trigger.schedule_reconstruction(now=now)

        mock_scheduler_client.update_schedule.assert_called_once()
        call_kwargs = mock_scheduler_client.update_schedule.call_args.kwargs
        assert call_kwargs["Name"] == "test-reconstruct-debounce"
        assert call_kwargs["ScheduleExpression"] == "at(2026-07-19T12:00:30)"
        assert call_kwargs["Target"] == {
            "Arn": "arn:aws:lambda:us-east-1:123:function:reconstruct",
            "RoleArn": "arn:aws:iam::123:role/scheduler-role",
        }
        assert call_kwargs["ActionAfterCompletion"] == "DELETE"
        assert not mock_scheduler_client.create_schedule.called

    def test_creates_schedule_when_none_exists_yet(self, mock_scheduler_client):
        mock_scheduler_client.update_schedule.side_effect = (
            FakeResourceNotFoundException()
        )
        now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

        reconstruction_trigger.schedule_reconstruction(now=now)

        mock_scheduler_client.create_schedule.assert_called_once()
        call_kwargs = mock_scheduler_client.create_schedule.call_args.kwargs
        assert call_kwargs["Name"] == "test-reconstruct-debounce"
        assert call_kwargs["ScheduleExpression"] == "at(2026-07-19T12:00:30)"

    def test_raises_without_required_env_vars(self, monkeypatch):
        monkeypatch.delenv("RECONSTRUCT_SCHEDULE_NAME", raising=False)
        monkeypatch.delenv("RECONSTRUCT_LAMBDA_ARN", raising=False)
        monkeypatch.delenv("RECONSTRUCT_SCHEDULER_ROLE_ARN", raising=False)

        with pytest.raises(ValueError, match="RECONSTRUCT_"):
            reconstruction_trigger.schedule_reconstruction()
