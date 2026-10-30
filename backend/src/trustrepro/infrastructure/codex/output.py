"""Collect only the pinned harness's public message and reasoning notifications."""

import time

from trustrepro.services import messages
from trustrepro.services.runtime import authenticate, fence

REASONING_DELTAS = {
    "item/reasoning/textDelta": ("text", "contentIndex"),
    "item/reasoning/summaryTextDelta": ("summary", "summaryIndex"),
}


def collect(db, row, event, *, recovered=False):
    method, data = event["method"], event.get("params", {})
    turn_id = data.get("turnId", row.turn_id)
    if row.turn_id and turn_id != row.turn_id:
        return
    if method in REASONING_DELTAS:
        channel, index_key = REASONING_DELTAS[method]
        return messages.thinking(
            db,
            row,
            data["itemId"],
            turn_id=turn_id,
            channel=channel,
            index=data[index_key],
            delta=data["delta"],
            emit=False,
        )
    if method == "item/reasoning/summaryPartAdded":
        return messages.thinking(
            db,
            row,
            data["itemId"],
            turn_id=turn_id,
            channel="summary",
            index=data["summaryIndex"],
            delta="",
            emit=False,
        )
    if method == "item/agentMessage/delta":
        return messages.visible(
            db,
            row,
            data["itemId"],
            data["delta"],
            True,
            turn_id=turn_id,
            emit=False,
        )
    if method in {"item/started", "item/completed"}:
        item = data.get("item", {})
        completed = method == "item/completed"
        if item.get("type") == "reasoning":
            return messages.thinking(
                db,
                row,
                item["id"],
                turn_id=turn_id,
                snapshot=item,
                completed=completed,
                recovered=recovered,
                emit=False,
            )
        if item.get("type") == "agentMessage" and completed:
            return messages.visible(
                db,
                row,
                item["id"],
                item.get("text", ""),
                turn_id=turn_id,
                emit=False,
            )


class NativeOutput:
    def __init__(self, factory, token, owner, version):
        self.factory, self.token, self.owner, self.version = factory, token, owner, version
        self.pending = []
        self.last_flush = time.monotonic()

    def push(self, event):
        method, item = event["method"], event.get("params", {}).get("item", {})
        accepted = (
            method in REASONING_DELTAS
            or method
            in {
                "item/agentMessage/delta",
                "item/reasoning/summaryPartAdded",
            }
            or method in {"item/started", "item/completed"}
            and item.get("type")
            in {
                "reasoning",
                "agentMessage",
            }
        )
        if accepted:
            self.pending.append(event)
        return accepted

    def flush(self, force=False):
        if not self.pending or not force and time.monotonic() - self.last_flush < 0.1:
            return
        with self.factory.begin() as db:
            row = authenticate(db, self.token, lock=True)
            fence(row, self.owner, self.version)
            changed = {}
            for event in self.pending:
                value = collect(db, row, event)
                if value is not None:
                    changed[value.id] = value
            for value in changed.values():
                messages.publish(db, value)
        self.pending.clear()
        self.last_flush = time.monotonic()


async def reconcile(factory, token, client, thread_id):
    with factory() as db:
        row = authenticate(db, token)
        turn_id = row.turn_id
        unfinished = (
            any(
                messages.native(m.blocks)["turnStatus"] != "completed"
                for m in messages.turn_messages(db, row)
            )
            if turn_id
            else False
        )
    if not turn_id or not unfinished:
        return
    cursor = None
    while True:
        params = {"threadId": thread_id, "turnId": turn_id, "limit": 100, "sortDirection": "asc"}
        if cursor:
            params["cursor"] = cursor
        page = await client.request("thread/items/list", params)
        with factory.begin() as db:
            row = authenticate(db, token, lock=True)
            for entry in page.get("data", []):
                item = entry.get("item", entry)
                value = collect(
                    db,
                    row,
                    {
                        "method": "item/completed",
                        "params": {"turnId": turn_id, "item": item},
                    },
                    recovered=True,
                )
                if value is not None:
                    messages.publish(db, value)
            messages.end_turn(db, row, "interrupted", turn_id=turn_id, observed=False)
        cursor = page.get("nextCursor")
        if not cursor:
            break
