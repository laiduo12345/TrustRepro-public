"""Public native output, independently persisted from harness internals and business state."""

import copy
import hashlib

from sqlalchemy import select

from trustrepro.infrastructure.db.models import Message, now_ms


def native(blocks):
    return next((b for b in blocks or [] if b.get("type") == "native"), None)


def reasoning(blocks):
    return next((b for b in blocks or [] if b.get("type") == "reasoning"), None)


def identifier(session_id, item_id):
    return "codex-" + hashlib.sha256((session_id + item_id).encode()).hexdigest()


def publish(db, message):
    from trustrepro.services.application import event

    db.flush()
    event(
        db,
        message.conversation_id,
        "message.delta",
        {
            "messageId": message.id,
            "role": message.role,
            "content": message.content,
            "blocks": message.blocks,
            "status": message.status,
            "createdAt": message.created_at,
            "offset": len(message.content),
        },
    )


def get_message(db, row, item_id, turn_id=None, model=None):
    value = db.get(Message, identifier(row.id, item_id))
    if value is None:
        value = Message(
            id=identifier(row.id, item_id),
            conversation_id=row.conversation_id,
            role="assistant",
            content="",
            blocks=[],
            status="streaming",
        )
        db.add(value)
    if not native(value.blocks):
        value.blocks = [
            *value.blocks,
            {
                "type": "native",
                "sessionId": row.id,
                "turnId": turn_id or row.turn_id,
                "itemId": item_id,
                "model": model or row.model_binding.get("model", ""),
                "turnStatus": "streaming",
            },
        ]
    return value


def visible(db, row, item_id, text, delta=False, *, turn_id=None, model=None, emit=True):
    message = get_message(db, row, item_id, turn_id, model)
    message.content = message.content + text if delta else text
    message.status = "streaming" if delta else "completed"
    if emit:
        publish(db, message)
    return message


def stop_interval(block, stamp):
    for interval in block.get("intervals", []):
        if interval.get("finishedAt") is None:
            interval["finishedAt"] = max(interval["startedAt"], stamp)


def thinking(
    db,
    row,
    item_id,
    *,
    turn_id=None,
    model=None,
    channel=None,
    index=0,
    delta=None,
    snapshot=None,
    completed=False,
    recovered=False,
    emit=True,
):
    message = get_message(db, row, item_id, turn_id, model)
    blocks = copy.deepcopy(message.blocks)
    block = reasoning(blocks)
    stamp = now_ms()
    if block is None:
        block = {
            "type": "reasoning",
            "text": [],
            "summary": [],
            "status": "streaming",
            "startedAt": None if recovered or completed else stamp,
            "finishedAt": None,
            "intervals": [],
        }
        blocks.append(block)
    # Native history supplies text, but cannot reconstruct elapsed time before observation.
    if (
        not recovered
        and not completed
        and not any(v.get("finishedAt") is None for v in block["intervals"])
    ):
        block["intervals"].append({"startedAt": stamp, "finishedAt": None})
        block["status"] = "streaming"
        block["finishedAt"] = None
    if snapshot is not None:
        for key in ("text", "summary"):
            # ThreadItem calls the raw text array `content`.
            source_key = "content" if key == "text" else key
            values = snapshot.get(source_key, [])
            if source_key in snapshot or recovered:
                block[key] = list(values)
    if channel and delta is not None and index >= 0:
        while len(block[channel]) <= index:
            block[channel].append("")
        block[channel][index] += delta
    last_observed = block.get("updatedAt", stamp)
    block["updatedAt"] = last_observed if recovered else stamp
    if completed:
        stop_interval(block, last_observed if recovered else stamp)
        block["status"] = "completed"
        block["finishedAt"] = stamp if not recovered else block.get("finishedAt")
    message.blocks = blocks
    message.status = block["status"]
    if emit:
        publish(db, message)
    return message


def turn_messages(db, row, turn_id=None):
    # The native block is first for newly collected messages. No legacy rows are rewritten.
    return list(
        db.scalars(
            select(Message)
            .where(
                Message.conversation_id == row.conversation_id,
                Message.blocks[0]["sessionId"].as_string() == row.id,
                Message.blocks[0]["turnId"].as_string() == (turn_id or row.turn_id),
            )
            .order_by(Message.created_at, Message.id)
        )
    )


def suspend(db, row):
    for message in turn_messages(db, row):
        blocks = copy.deepcopy(message.blocks)
        block = reasoning(blocks)
        if block and any(i.get("finishedAt") is None for i in block["intervals"]):
            stop_interval(block, now_ms())
            message.blocks = blocks
            publish(db, message)


def end_turn(db, row, status, *, turn_id=None, observed=True):
    for message in turn_messages(db, row, turn_id):
        blocks = copy.deepcopy(message.blocks)
        meta, block = native(blocks), reasoning(blocks)
        if meta["turnStatus"] == "completed" and status != "completed":
            continue
        if meta["turnStatus"] == status and message.status != "streaming":
            continue
        meta["turnStatus"] = status
        if block and block["status"] == "streaming":
            stamp = now_ms() if observed else block.get("updatedAt", message.created_at)
            stop_interval(block, stamp)
            block["status"], block["finishedAt"] = status, stamp
        if message.status == "streaming":
            message.status = status
        message.blocks = blocks
        publish(db, message)
