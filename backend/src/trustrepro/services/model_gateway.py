"""Responses passthrough with one fixed-precision observable usage ledger."""

import asyncio
import json
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.models import Account, ModelRequest, Run, new_id, now_ms
from trustrepro.services import preferences
from trustrepro.services.model_estimate import reservation
from trustrepro.services.model_http import request_options
from trustrepro.services.runtime import authenticate, require_budget


def settle(factory, identifier, settings, usage, response_id=None):
    with factory.begin() as db:
        row = db.get(ModelRequest, identifier, with_for_update=True)
        settle_row(row, settings, usage, response_id)


def settle_row(row, settings, usage, response_id=None):
    if row.status == "SETTLED":
        return
    row.finished_at = now_ms()
    if usage is None:
        row.status = "UNCERTAIN"
        return
    row.usage, row.provider_response_id = usage, response_id
    pricing = (
        row.pricing
        if all(k in (row.pricing or {}) for k in ("inputRate", "outputRate"))
        else settings.model_pricing
    )
    if pricing.get("currency", "CNY") != "CNY":
        raise BusinessError("CURRENCY_MISMATCH", "请求费用快照尚未完成人民币迁移")
    row.pricing = {**(row.pricing or {}), **pricing, "currency": "CNY"}
    row.actual_cost = (
        Decimal(usage.get("input_tokens", 0)) * Decimal(str(pricing["inputRate"]))
        + Decimal(usage.get("output_tokens", 0)) * Decimal(str(pricing["outputRate"]))
    ) / 1000000
    row.status = "SETTLED"


async def forward(factory, settings, credential, path, raw, request_headers=None):
    if path not in {"responses", "responses/compact"}:
        raise BusinessError("UNSUPPORTED_PROTOCOL", "仅支持 Responses 协议")
    body, identifier = json.loads(raw), new_id("model")
    with factory() as db:
        owner = authenticate(db, credential)
        config, encrypted = preferences.request_config(db, owner, settings)
    key = preferences.cipher(settings).decrypt(encrypted.encode()).decode() if encrypted else ""
    if body.get("model", config["model"]) != config["model"]:
        raise BusinessError("MODEL_MISMATCH", "请求模型与会话绑定不一致")
    amount, estimate = reservation(body, config, settings.unknown_reservation_cny)
    with factory.begin() as db:
        binding = authenticate(db, credential, lock=True)
        db.get(Account, binding.account_id, with_for_update=True)
        scope = binding.kind
        if binding.desired_state != "ACTIVE":
            raise BusinessError("RUN_STOPPED", "会话已暂停，不发起新模型请求")
        if binding.kind == "execution":
            run = db.get(Run, binding.run_id)
            final_reply = binding.state.get("completionSubmitted") and run.status in {
                "COMPLETED",
                "FAILED",
            }
            if run.status != "RUNNING" and not final_reply:
                raise BusinessError("RUN_STOPPED", "运行已停止，不发起新模型请求")
        require_budget(db, binding.account_id, scope, amount)
        db.add(
            ModelRequest(
                id=identifier,
                session_id=binding.id,
                account_id=binding.account_id,
                conversation_id=binding.conversation_id,
                purpose="compact"
                if path.endswith("compact") or binding.state.get("phase") == "compacting"
                else binding.kind,
                status="REQUESTED",
                reserved_cost=amount,
                usage_scope="research" if scope == "research" else "reproduction",
                pricing={
                    **{k: config[k] for k in ("inputRate", "outputRate", "currency")},
                    "reservationEstimate": estimate,
                },
                connection=config,
                owner_version=binding.owner_version,
                lease_until=now_ms() + 60000,
            )
        )
    root = settings.var_root / "model-requests" / identifier
    root.mkdir(parents=True)
    (root / "request.json").write_bytes(raw)
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30), trust_env=False)
    headers = {
        k: v
        for k, v in (request_headers or {}).items()
        if k.lower()
        not in {
            "host",
            "authorization",
            "api-key",
            "content-length",
            "connection",
            "transfer-encoding",
            "cookie",
            "accept-encoding",
            "proxy-authorization",
        }
    }
    headers.update(preferences.headers(config, key))

    async def pulse():
        while True:
            await asyncio.sleep(3)
            try:
                with factory.begin() as db:
                    row = db.get(ModelRequest, identifier, with_for_update=True)
                    if row.status != "REQUESTED":
                        return
                    row.lease_until = now_ms() + 60000
            except SQLAlchemyError:
                continue

    heartbeat_task = asyncio.create_task(pulse())

    def save_usage(usage, response_id=None):
        receipt = root / "usage.part"
        receipt.write_text(
            json.dumps({"usage": usage, "responseId": response_id}), encoding="utf-8"
        )
        receipt.replace(root / "usage.json")
        try:
            settle(factory, identifier, settings, usage, response_id)
        except SQLAlchemyError:
            # The durable receipt is picked up after the database recovers.
            pass

    try:
        upstream = await client.send(
            client.build_request(
                "POST",
                **request_options(settings, config["baseUrl"].rstrip("/") + "/" + path, headers),
                content=raw,
            ),
            stream=True,
        )
    except (Exception, asyncio.CancelledError):
        try:
            save_usage(None)
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            await client.aclose()
        raise

    async def stream():
        usage, response_id, buffer = None, None, b""
        try:
            with (root / "response.bin").open("wb") as out:
                async for chunk in upstream.aiter_bytes():
                    out.write(chunk)
                    out.flush()
                    buffer += chunk
                    if body.get("stream"):
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            if line.startswith(b"data:"):
                                try:
                                    result = json.loads(line[5:])
                                    result = result.get("response", result)
                                    if result.get("usage") is not None:
                                        usage, response_id = result["usage"], result.get("id")
                                except (ValueError, AttributeError):
                                    pass
                    yield chunk
            if not body.get("stream"):
                result = json.loads(buffer)
                usage, response_id = result.get("usage"), result.get("id")
        finally:
            try:
                save_usage(usage, response_id)
            finally:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
                await upstream.aclose()
                await client.aclose()

    return (
        stream(),
        upstream.status_code,
        {
            k: v
            for k, v in upstream.headers.items()
            if k.lower()
            not in {
                "connection",
                "transfer-encoding",
                "content-length",
                "content-encoding",
                "set-cookie",
            }
        },
        identifier,
    )


def recover_requests(factory, settings):
    with factory() as db:
        ids = list(
            db.scalars(
                select(ModelRequest.id)
                .where(
                    ModelRequest.status == "REQUESTED",
                    ModelRequest.lease_until < now_ms(),
                )
                .limit(100)
            )
        )
    for identifier in ids:
        root = settings.var_root / "model-requests" / identifier
        usage, response_id = None, None
        try:
            if (root / "usage.json").exists():
                receipt = json.loads((root / "usage.json").read_text(encoding="utf-8"))
                usage, response_id = receipt["usage"], receipt.get("responseId")
            elif (root / "response.bin").exists():
                with (root / "response.bin").open("rb") as source:
                    for line in source:
                        try:
                            item = json.loads(line[5:] if line.startswith(b"data:") else line)
                            item = item.get("response", item)
                            if item.get("usage") is not None:
                                usage, response_id = item["usage"], item.get("id")
                        except (ValueError, AttributeError):
                            pass
        except (OSError, ValueError, KeyError):
            pass
        with factory.begin() as db:
            row = db.get(ModelRequest, identifier, with_for_update=True)
            if row.status != "REQUESTED" or row.lease_until >= now_ms():
                continue
            settle_row(row, settings, usage, response_id)
