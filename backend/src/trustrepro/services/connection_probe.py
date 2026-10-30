"""Explicit, idempotent Responses streaming/tool test; never retries a request."""

import json
from decimal import Decimal

import httpx

from trustrepro.domain.errors import BusinessError
from trustrepro.domain.money import CURRENCY, from_usd
from trustrepro.infrastructure.db.models import ConnectionTest, ModelConnection
from trustrepro.services.model_http import request_options
from trustrepro.services.preferences import binding, headers


async def probe(factory, secret_cipher, connection_id, request_id, model, settings):
    with factory.begin() as db:
        old = db.get(ConnectionTest, request_id, with_for_update=True)
        if old:
            if old.connection_id != connection_id or old.result["connection"]["model"] != model:
                raise BusinessError("IDEMPOTENCY_CONFLICT", "测试标识已被使用")
            return {"status": old.status, **old.result}
        row = db.get(ModelConnection, connection_id)
        if row is None or row.deleted:
            raise BusinessError("NOT_FOUND", "模型连接不存在")
        config, encrypted = binding(row, model), row.secret
        db.add(
            ConnectionTest(
                id=request_id,
                connection_id=row.id,
                status="REQUESTED",
                result={"connection": config, "calls": []},
            )
        )
    key = secret_cipher.decrypt(encrypted.encode()).decode() if encrypted else ""
    calls = []
    items = [{"role": "user", "content": "Call connection_echo with text ok, then reply ok."}]
    tool = {
        "type": "function",
        "name": "connection_echo",
        "description": "Return the provided text.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "strict": True,
    }
    status, diagnostic = "PASSED", "流式响应与工具往返通过"
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            for step in range(2):
                call = {
                    "status": "REQUESTED",
                    "reservedCost": str(from_usd("0.15")),
                    "currency": CURRENCY,
                }
                calls.append(call)
                with factory.begin() as db:
                    db.get(ConnectionTest, request_id).result = {
                        "connection": config,
                        "calls": json.loads(json.dumps(calls)),
                    }
                completed, streamed = None, False
                async with client.stream(
                    "POST",
                    **request_options(
                        settings,
                        config["baseUrl"] + "/responses",
                        headers(config, key),
                    ),
                    json={
                        "model": config["model"],
                        "input": items,
                        "tools": [tool],
                        "tool_choice": {"type": "function", "name": "connection_echo"}
                        if step == 0
                        else "none",
                        "stream": True,
                        "store": False,
                        "max_output_tokens": 512,
                    },
                ) as response:
                    if response.status_code >= 400:
                        call["status"] = "UNCERTAIN"
                        raise BusinessError(
                            "PROBE_HTTP", f"Responses 返回 HTTP {response.status_code}"
                        )
                    async for line in response.aiter_lines():
                        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                            continue
                        item = json.loads(line[5:])
                        streamed = streamed or item.get("type", "").endswith(".delta")
                        if item.get("type") == "response.completed":
                            completed = item["response"]
                if completed is None:
                    raise BusinessError("PROBE_PROTOCOL", "未收到 response.completed")
                usage = completed.get("usage")
                call.update(status="SETTLED" if usage is not None else "UNCERTAIN", usage=usage)
                if usage is not None:
                    call["estimatedCost"] = str(
                        (
                            Decimal(usage.get("input_tokens", 0)) * Decimal(config["inputRate"])
                            + Decimal(usage.get("output_tokens", 0)) * Decimal(config["outputRate"])
                        )
                        / 1000000
                    )
                if not streamed:
                    raise BusinessError("PROBE_STREAM", "未观察到增量流式事件")
                output = completed.get("output", [])
                if step == 0:
                    functions = [v for v in output if v.get("type") == "function_call"]
                    if len(functions) != 1 or functions[0].get("name") != "connection_echo":
                        raise BusinessError("PROBE_TOOL", "模型未返回预期工具调用")
                    function = functions[0]
                    if json.loads(function["arguments"]).get("text") != "ok":
                        raise BusinessError("PROBE_TOOL", "工具参数不符合测试要求")
                    items += output + [
                        {
                            "type": "function_call_output",
                            "call_id": function["call_id"],
                            "output": "ok",
                        }
                    ]
                elif not any(v.get("type") == "message" for v in output):
                    raise BusinessError("PROBE_TOOL", "工具回执后没有公开回复")
    except Exception as error:
        status = "FAILED"
        diagnostic = (
            error.message
            if isinstance(error, BusinessError)
            else "连接测试未完成：" + type(error).__name__
        )
        for call in calls:
            if call["status"] == "REQUESTED":
                call["status"] = "UNCERTAIN"
    result = {"connection": config, "calls": calls, "message": diagnostic}
    with factory.begin() as db:
        row = db.get(ConnectionTest, request_id)
        row.status, row.result = status, result
    return {"status": status, **result}
