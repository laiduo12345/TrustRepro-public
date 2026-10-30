"""Read a provider's model catalog without persisting draft credentials or using inference."""

import json
from decimal import Decimal, InvalidOperation

import httpx

from trustrepro.domain.errors import BusinessError
from trustrepro.domain.money import from_usd
from trustrepro.infrastructure.db.models import ModelConnection
from trustrepro.services.model_http import request_options
from trustrepro.services.preferences import exchange_rate, headers


async def discover(factory, secret_cipher, value, settings):
    encrypted = ""
    with factory() as db:
        rate_to_cny = exchange_rate(db)
    if value.connectionId:
        with factory() as db:
            row = db.get(ModelConnection, value.connectionId)
            if row is None or row.deleted:
                raise BusinessError("NOT_FOUND", "提供商不存在")
            if row.version != value.expectedVersion:
                raise BusinessError("VERSION_CONFLICT", "提供商已被修改，请重新打开设置")
            if all(row.config.get(k) == getattr(value, k) for k in ("provider", "baseUrl", "auth")):
                encrypted = row.secret
    key = (
        value.apiKey.get_secret_value()
        if value.apiKey is not None
        else (secret_cipher.decrypt(encrypted.encode()).decode() if encrypted else "")
    )
    if value.auth != "none" and not key:
        raise BusinessError("API_KEY_REQUIRED", "请为当前 API 地址填写密钥")
    models, after = {}, None
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client:
            while True:
                async with client.stream(
                    "GET",
                    **request_options(
                        settings,
                        value.baseUrl + "/models",
                        headers(value.model_dump(exclude={"apiKey"}), key),
                    ),
                    params={"after": after} if after else None,
                ) as response:
                    if response.status_code != 200:
                        hints = {
                            401: "密钥无效或已过期",
                            403: "密钥没有模型列表权限",
                            404: "此地址未提供模型列表，可手动添加",
                            429: "请求过于频繁，请稍后重试",
                        }
                        raise BusinessError(
                            "MODEL_LIST_HTTP",
                            f"获取模型失败（HTTP {response.status_code}）："
                            + hints.get(response.status_code, "请检查 API 地址或服务状态"),
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 10 * 1024 * 1024:
                            raise BusinessError(
                                "MODEL_LIST_SIZE", "模型列表响应过大，可手动添加模型"
                            )
                payload = json.loads(body)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise BusinessError("MODEL_LIST_FORMAT", "服务未返回 data 模型列表，可手动添加")
                before = len(models)
                for item in payload["data"]:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                        continue
                    identifier = item["id"].strip()
                    if not identifier or len(identifier) > 200:
                        continue
                    result = {"model": identifier}
                    # OpenRouter documents prices per token; our configuration uses per million.
                    pricing = item.get("pricing")
                    if value.provider == "openrouter" and isinstance(pricing, dict):
                        for source, target in (
                            ("prompt", "inputRate"),
                            ("completion", "outputRate"),
                        ):
                            try:
                                rate = from_usd(
                                    Decimal(str(pricing.get(source))) * 1000000, rate_to_cny
                                )
                                if rate.is_finite() and rate >= 0:
                                    result[target] = str(rate)
                            except (InvalidOperation, ValueError):
                                pass
                    models.setdefault(identifier, result)
                if not payload.get("has_more"):
                    break
                cursor = payload.get("last_id")
                if not isinstance(cursor, str) or cursor == after or len(models) == before:
                    raise BusinessError(
                        "MODEL_LIST_FORMAT", "模型列表分页无进展，请手动添加或稍后重试"
                    )
                after = cursor
    except httpx.TimeoutException:
        raise BusinessError("MODEL_LIST_TIMEOUT", "获取模型列表超时，可重试或手动添加") from None
    except httpx.HTTPError:
        raise BusinessError(
            "MODEL_LIST_NETWORK", "无法连接模型服务，请检查 API 地址与网络"
        ) from None
    except (ValueError, UnicodeError):
        raise BusinessError("MODEL_LIST_FORMAT", "服务未返回有效模型列表，可手动添加") from None
    return {"models": sorted(models.values(), key=lambda m: m["model"].casefold())}
