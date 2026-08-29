import asyncio
from collections.abc import Callable
from typing import Any


def make_raw_message_update(
    *,
    update_id: int,
    sender_id: int,
    sender_name: str,
    text: str,
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 7,
            "date": 1_754_000_000,
            "from": {
                "id": sender_id,
                "is_bot": False,
                "first_name": sender_name,
            },
            "chat": {"id": sender_id, "type": "private"},
            "text": text,
        },
    }


async def wait_until(
    condition: Callable[[], bool],
    *,
    timeout: float,
) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), timeout=timeout)
