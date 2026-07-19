"""Debounced trigger for canon-doc reconstruction from the fact store.

Reconstruction runs as its own Lambda function (a separate container command
pointed at the same image as the main bot handler -- see reconstruct_handler
in handler.py and the aws_lambda_function.reconstruct Terraform resource).
It's invoked by a one-time EventBridge Schedule rather than a fixed interval:
every successful fact write pushes the schedule's fire time out by
DEBOUNCE_MINUTES. Since creating a schedule with a name that already exists
just moves its fire time, a burst of facts (e.g. one /refresh-songs run
queuing many songs) collapses into a single reconstruction once things go
quiet, instead of one run per fact.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import boto3

logger = logging.getLogger()

DEBOUNCE_MINUTES = 5


def schedule_reconstruction(now: Optional[datetime] = None) -> None:
    """Push the reconstruction debounce schedule out by DEBOUNCE_MINUTES.

    Creates the one-time EventBridge Schedule if it doesn't exist yet,
    otherwise updates its fire time. Callers should treat failures here as
    non-fatal (log and move on) -- the fact itself is already durably
    written by the time this runs, and a missed/late reconstruction just
    means the canon doc catches up on the next successful fact write.
    """
    schedule_name = os.getenv("RECONSTRUCT_SCHEDULE_NAME")
    lambda_arn = os.getenv("RECONSTRUCT_LAMBDA_ARN")
    role_arn = os.getenv("RECONSTRUCT_SCHEDULER_ROLE_ARN")
    if not schedule_name or not lambda_arn or not role_arn:
        raise ValueError(
            "RECONSTRUCT_SCHEDULE_NAME, RECONSTRUCT_LAMBDA_ARN, and "
            "RECONSTRUCT_SCHEDULER_ROLE_ARN must all be set"
        )

    fire_at = (now or datetime.now(timezone.utc)) + timedelta(minutes=DEBOUNCE_MINUTES)
    schedule_expression = f"at({fire_at.strftime('%Y-%m-%dT%H:%M:%S')})"

    scheduler = boto3.client("scheduler")
    kwargs = dict(
        Name=schedule_name,
        ScheduleExpression=schedule_expression,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={"Arn": lambda_arn, "RoleArn": role_arn},
        ActionAfterCompletion="DELETE",
    )
    try:
        scheduler.update_schedule(**kwargs)
        logger.info(f"Pushed back reconstruction debounce to {fire_at.isoformat()}")
    except scheduler.exceptions.ResourceNotFoundException:
        scheduler.create_schedule(**kwargs)
        logger.info(
            f"Created reconstruction debounce schedule for {fire_at.isoformat()}"
        )
