"""Step S2: cost guards. Daily counters that stop anyone running up the AWS bill.

Each counter is one tiny DynamoDB row per day, e.g. LIMIT#uploads#2026-11-08.
DynamoDB adds 1 only if the count is still below the limit, in a single atomic step,
so even many uploads arriving at the same moment cannot slip past the cap.
"""

import time

from botocore.exceptions import ClientError

MAX_FILE_BYTES = 5 * 1024 * 1024        # 5 MB: far larger than any real PO, far smaller than an abuse file


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())   # the day resets at midnight UTC


def take_slot(table, counter: str, limit: int) -> bool:
    """Use one of today's slots. False means the daily limit is already reached."""
    try:
        table.update_item(
            Key={"po_id": f"LIMIT#{counter}#{_today()}"},
            UpdateExpression="ADD used :one",
            ConditionExpression="attribute_not_exists(used) OR used < :limit",
            ExpressionAttributeValues={":one": 1, ":limit": limit},
        )
        return True
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
