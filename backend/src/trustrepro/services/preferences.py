"""User configuration, versioned model bindings and API-only credentials."""

import copy
import os
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select

from trustrepro.domain.errors import BusinessError
from trustrepro.domain.money import CURRENCY, DEFAULT_USD_TO_CNY
from trustrepro.domain.runtime import Allocation
from trustrepro.infrastructure.db.models import (
    AgentSession,
    ConnectionTest,
    Conversation,
    ModelConnection,
    ModelRequest,
    UserSettings,
)

PRESETS = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "baseUrl": "https://api.deepseek.com",
        "auth": "bearer",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "docsUrl": "https://api-docs.deepseek.com/guides/responses_api/",
    },
    {
        "id": "glm",
        "name": "智谱 GLM",
        "baseUrl": "https://open.bigmodel.cn/api/v1",
        "auth": "bearer",
        "models": ["glm-5.3"],
        "note": "使用支持 Responses 的 GLM Coding Plan 密钥；标准 API 密钥不通用。",
        "docsUrl": "https://docs.bigmodel.cn/cn/coding-plan/tool/codex",
    },
    {
        "id": "kimi",
        "name": "Kimi（月之暗面）",
        "baseUrl": "https://api.moonshot.cn/v1",
        "auth": "bearer",
        "models": ["kimi-k3"],
        "note": "默认中国站地址，需使用中国站 API Key。",
        "docsUrl": "https://www.kimi.ai/zh-hans/resources/codex-api",
    },
    {
        "id": "qwen",
        "name": "通义千问（阿里云百炼）",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "auth": "bearer",
        "models": ["qwen3.8-max"],
        "note": "默认北京地域；也可填写工作空间专属地址。",
        "docsUrl": "https://help.aliyun.com/en/model-studio/compatibility-with-openai-responses-api",
    },
    {
        "id": "cliproxy",
        "name": "CLIProxyAPI",
        "baseUrl": "http://host.docker.internal:8317/v1",
        "auth": "bearer",
    },
    {"id": "openai", "name": "OpenAI", "baseUrl": "https://api.openai.com/v1", "auth": "bearer"},
    {
        "id": "azure",
        "name": "Azure OpenAI",
        "baseUrl": "https://YOUR-RESOURCE.openai.azure.com/openai/v1",
        "auth": "api-key",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "baseUrl": "https://openrouter.ai/api/v1",
        "auth": "bearer",
    },
    {"id": "custom", "name": "自定义 Responses", "baseUrl": "", "auth": "bearer"},
]


class ProviderAPI(BaseModel):
    currency: Literal["CNY"] = "CNY"
    provider: str = "custom"
    baseUrl: str
    auth: str = "bearer"
    apiKey: SecretStr | None = None

    @field_validator("baseUrl")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("请输入不含凭证和查询参数的 HTTP(S) API 地址")
        return value.rstrip("/")

    @field_validator("auth")
    @classmethod
    def valid_auth(cls, value):
        if value not in {"bearer", "api-key", "none"}:
            raise ValueError("不支持的鉴权方式")
        return value


class ProviderModel(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    inputRate: Decimal = Field(ge=0, allow_inf_nan=False)
    outputRate: Decimal = Field(ge=0, allow_inf_nan=False)

    @field_validator("model")
    @classmethod
    def valid_model(cls, value):
        if not value.strip():
            raise ValueError("请输入模型 ID")
        return value.strip()


class ConnectionInput(ProviderAPI):
    name: str = Field(min_length=1, max_length=100)
    models: list[ProviderModel]
    enabled: bool = True
    expectedVersion: int = 0

    @field_validator("models")
    @classmethod
    def unique_models(cls, value):
        if len({m.model for m in value}) != len(value):
            raise ValueError("同一提供商不能重复添加模型 ID")
        return value


class ModelDiscoveryInput(ProviderAPI):
    connectionId: str | None = None
    expectedVersion: int = 0


ModelRole = Literal["research", "execution"]
ROLES = ("research", "execution")
ACTIVE_MODEL_STATES = {"RUNNING", "QUEUED", "WAITING_JOB", "RECOVERING"}


class ModelSelection(BaseModel):
    connectionId: str
    model: str = Field(min_length=1, max_length=200)

    @field_validator("model")
    @classmethod
    def nonempty_model(cls, value):
        if not value.strip():
            raise ValueError("请输入模型 ID")
        return value.strip()


class ModelSelections(BaseModel):
    research: ModelSelection
    execution: ModelSelection


class DefaultsInput(BaseModel):
    expectedRevision: int
    models: ModelSelections
    allocation: Allocation
    usdToCnyRate: Decimal = Field(default=DEFAULT_USD_TO_CNY, gt=0, le=1000000, allow_inf_nan=False)


def cipher(settings):
    root = settings.secrets_root
    root.mkdir(parents=True, exist_ok=True)
    path = root / "master.key"
    if not path.exists():
        key = Fernet.generate_key()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as out:
                out.write(key)
    return Fernet(path.read_bytes())


def bootstrap(db, settings, encrypted_key):
    for test in db.scalars(select(ConnectionTest).where(ConnectionTest.status == "REQUESTED")):
        test.status = "UNCERTAIN"
        calls = [
            {**c, "status": "UNCERTAIN"} if c["status"] == "REQUESTED" else c
            for c in test.result.get("calls", [])
        ]
        test.result = {
            **test.result,
            "calls": calls,
            "message": "测试连接中断，未知费用预留已保留；不会自动重发",
        }
    row = db.get(UserSettings, "default", with_for_update=True)
    if row:
        return
    if not settings.bootstrap_model:
        db.add(
            UserSettings(
                id="default",
                revision=1,
                content={
                    "models": {role: {"connectionId": "", "model": ""} for role in ROLES},
                    "allocation": Allocation().model_dump(),
                    "usdToCnyRate": str(settings.usd_to_cny_rate),
                },
            )
        )
        return
    connection = ModelConnection(
        id="connection-default",
        name="CLIProxyAPI",
        version=1,
        config={
            "provider": "cliproxy",
            "baseUrl": settings.model_base_url.rstrip("/"),
            "auth": "bearer",
            "models": [
                {
                    "model": settings.model_name,
                    **{k: settings.model_pricing[k] for k in ("inputRate", "outputRate")},
                }
            ],
            "currency": CURRENCY,
        },
        secret=encrypted_key,
        enabled=True,
    )
    db.add(connection)
    db.flush()
    db.add(
        UserSettings(
            id="default",
            revision=1,
            content={
                "models": {
                    role: {"connectionId": connection.id, "model": settings.model_name}
                    for role in ROLES
                },
                "allocation": Allocation().model_dump(),
                "usdToCnyRate": str(settings.usd_to_cny_rate),
            },
        )
    )
    for chat in db.scalars(select(Conversation)):
        if not chat.model_bindings or not any(chat.model_bindings.values()):
            chat.model_bindings = {role: binding(connection) for role in ROLES}
    for agent in db.scalars(select(AgentSession)):
        if not agent.model_binding:
            agent.model_binding = copy.deepcopy(
                db.get(Conversation, agent.conversation_id).model_bindings[model_role(agent.kind)]
            )
    for request in db.scalars(select(ModelRequest).where(ModelRequest.status != "SETTLED")):
        if not request.pricing:
            request.pricing = {
                k: binding(connection)[k] for k in ("inputRate", "outputRate", "currency")
            }


def binding(connection, model=None):
    models = connection.config["models"]
    selected = (
        next((m for m in models if m["model"] == model), None)
        if model
        else next(iter(models), None)
    )
    if selected is None:
        raise BusinessError("MODEL_NOT_CONFIGURED", "请先在提供商设置中添加该模型及费率")
    return {
        "connectionId": connection.id,
        "version": connection.version,
        **{k: copy.deepcopy(v) for k, v in connection.config.items() if k != "models"},
        **copy.deepcopy(selected),
        "currency": CURRENCY,
    }


def model_role(kind):
    return "research" if kind == "research" else "execution"


def selected_binding(db, choice):
    choice = ModelSelection.model_validate(choice)
    connection = db.get(ModelConnection, choice.connectionId)
    if not connection or connection.deleted or not connection.enabled:
        raise BusinessError("MODEL_DISABLED", "请选择可用的模型连接")
    return binding(connection, choice.model)


def default_bindings(db, choices=None):
    settings = db.get(UserSettings, "default")
    if choices is None and settings is None:
        return {}  # Isolated tests may construct the business store without an API.
    raw = choices or settings.content["models"]
    if any(not raw[role].get("connectionId") or not raw[role].get("model") for role in ROLES):
        raise BusinessError("MODEL_NOT_CONFIGURED", "请先在设置或新对话中选择研究与执行模型")
    values = ModelSelections.model_validate(raw).model_dump()
    return {role: selected_binding(db, values[role]) for role in ROLES}


def public_connection(row):
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        **row.config,
        "enabled": row.enabled,
        "keyConfigured": bool(row.secret),
    }


def snapshot(db):
    row = db.get(UserSettings, "default")
    if row is None:
        raise BusinessError("SETTINGS_NOT_READY", "模型设置尚未初始化")
    return {
        "revision": row.revision,
        "usdToCnyRate": str(DEFAULT_USD_TO_CNY),
        "currency": CURRENCY,
        **row.content,
        "presets": PRESETS,
        "connections": [
            public_connection(c)
            for c in db.scalars(select(ModelConnection).where(ModelConnection.deleted.is_(False)))
        ],
    }


def exchange_rate(db):
    row = db.get(UserSettings, "default")
    return (
        Decimal(str(row.content.get("usdToCnyRate", DEFAULT_USD_TO_CNY)))
        if row
        else DEFAULT_USD_TO_CNY
    )


def save_connection(db, identifier, value, encrypted_key=None):
    row = db.get(ModelConnection, identifier, with_for_update=True)
    if row and row.deleted:
        raise BusinessError("NOT_FOUND", "提供商已删除，请重新添加")
    if row and row.version != value.expectedVersion:
        raise BusinessError("VERSION_CONFLICT", "连接已被修改，请重新读取")
    if not row:
        if value.expectedVersion:
            raise BusinessError("NOT_FOUND", "模型连接不存在")
        row = ModelConnection(id=identifier, version=0, secret="")
        db.add(row)
    if (
        encrypted_key is None
        and row.config
        and any(
            row.config.get(key) != getattr(value, key) for key in ("baseUrl", "auth", "provider")
        )
    ):
        # A saved key is bound to its destination; changing provider must not
        # forward that credential to a different service.
        row.secret = ""
    row.name, row.enabled = value.name, value.enabled
    row.config = value.model_dump(
        mode="json", exclude={"name", "apiKey", "expectedVersion", "enabled"}
    )
    row.version += 1
    if encrypted_key is not None:
        row.secret = encrypted_key
    db.flush()
    return public_connection(row)


def delete_connection(db, identifier, expected_version):
    settings = db.get(UserSettings, "default", with_for_update=True)
    row = db.get(ModelConnection, identifier, with_for_update=True)
    if row is None or row.deleted:
        return {"deleted": True}
    if row.version != expected_version:
        raise BusinessError("VERSION_CONFLICT", "提供商已被修改，请重新打开后删除")
    row.deleted, row.enabled, row.secret = True, False, ""
    row.version += 1
    if settings:
        content = copy.deepcopy(settings.content)
        for role, choice in content["models"].items():
            if choice.get("connectionId") == identifier:
                content["models"][role] = {"connectionId": "", "model": ""}
        if content != settings.content:
            settings.content = content
            settings.revision += 1
    db.flush()
    return {"deleted": True}


def save_defaults(db, value):
    row = db.get(UserSettings, "default", with_for_update=True)
    default_bindings(db, value.models.model_dump())
    if row.revision != value.expectedRevision:
        raise BusinessError("VERSION_CONFLICT", "设置已被修改，请重新读取")
    row.content = value.model_dump(mode="json", exclude={"expectedRevision"})
    row.revision += 1
    return snapshot(db)


def public_bindings(db, chat):
    active = {
        model_role(a.kind)
        for a in db.scalars(select(AgentSession).where(AgentSession.conversation_id == chat.id))
        if a.status in ACTIVE_MODEL_STATES
    }
    return {
        role: {
            **{
                k: v
                for k, v in chat.model_bindings.get(role, {}).items()
                if k in {"connectionId", "version", "model"}
            },
            "canChange": role not in active,
        }
        for role in ROLES
    }


def switch_model(db, chat_id, role, connection_id, model):
    if role not in ROLES:
        raise BusinessError("INVALID_ROLE", "模型阶段无效")
    chat = db.get(Conversation, chat_id, with_for_update=True)
    if not chat:
        raise BusinessError("NOT_FOUND", "会话不存在")
    agents = list(
        db.scalars(
            select(AgentSession)
            .where(
                AgentSession.conversation_id == chat_id,
                AgentSession.kind.in_(
                    ["research"] if role == "research" else ["execution", "report"]
                ),
            )
            .with_for_update()
        )
    )
    if any(a.status in ACTIVE_MODEL_STATES for a in agents):
        raise BusinessError("TASK_ACTIVE", "请先暂停任务再切换模型")
    value = selected_binding(db, {"connectionId": connection_id, "model": model})
    chat.model_bindings = {**chat.model_bindings, role: value}
    for agent in agents:
        if agent.kind == "research" or agent.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            agent.model_binding = copy.deepcopy(value)
    from trustrepro.services.application import event

    event(db, chat_id)


def request_config(db, agent, settings):
    value = agent.model_binding
    if not value:
        # Only an uninitialised isolated business store uses environment defaults.
        return {
            "model": settings.model_name,
            "baseUrl": settings.model_base_url,
            "auth": "bearer",
            **settings.model_pricing,
        }, ""
    row = db.get(ModelConnection, value["connectionId"])
    if not row or row.deleted or not row.enabled:
        raise BusinessError("MODEL_DISABLED", "会话的提供商已停用或删除，请切换模型")
    if any(row.config.get(k) != value.get(k) for k in ("baseUrl", "auth")):
        raise BusinessError(
            "MODEL_CONNECTION_CHANGED", "连接地址或鉴权已变更，请重新选择该阶段的模型连接"
        )
    return copy.deepcopy(value), row.secret


def headers(config, key):
    result = {"Content-Type": "application/json"}
    if config["auth"] == "bearer":
        result["Authorization"] = "Bearer " + key
    elif config["auth"] == "api-key":
        result["api-key"] = key
    return result
