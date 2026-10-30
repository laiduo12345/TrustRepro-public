"""Small deterministic contracts; scientific choices belong to the Agent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trustrepro.domain.errors import BusinessError


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, allow_inf_nan=False)


class Resources(Contract):
    cpus: int = Field(default=2, ge=1)
    memoryGb: float = Field(default=4, gt=0)
    diskGb: float = Field(default=10, gt=0)
    gpus: Literal[0] = 0


class Allocation(Contract):
    currency: Literal["CNY"] = "CNY"
    maxCost: float = Field(default=67, ge=0, description="人民币费用上限（元）")
    maxHours: float = Field(default=2, gt=0)
    resources: Resources = Field(default_factory=Resources)


class ValueShape(Contract):
    type: Literal["string", "integer", "number", "boolean", "array", "object", "enum"]
    choices: list = Field(default_factory=list)
    items: "ValueShape | None" = None
    properties: dict[str, "ValueShape"] = Field(default_factory=dict)


class BoundField(Contract):
    key: str
    label: str
    binding: str = Field(pattern=r"^/parameters/.+$")
    shape: ValueShape


class Proposal(Contract):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1)
    methodology: str = Field(min_length=1)
    parameters: dict = Field(default_factory=dict)
    fields: list[BoundField] = Field(default_factory=list)
    sources: list[str] = Field(
        default_factory=list, description="使用 read_source/read_paper 返回的来源 id，不是 URL。"
    )
    deliverables: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    allocation: Allocation


def merged_ms(intervals):
    end, total = 0, 0
    for start, stop in sorted(intervals):
        total += max(0, stop - max(start, end))
        end = max(end, stop)
    return total


def bind_values(proposal, values):
    import copy

    result = copy.deepcopy(proposal)
    fields = {f["key"]: f for f in result["fields"]}
    if set(values) - set(fields):
        raise BusinessError("INVALID_CARD", "参数不存在")

    def valid(value, shape):
        kind = shape["type"]
        basic = {
            "string": isinstance(value, str),
            "integer": type(value) is int,
            "number": type(value) in {int, float},
            "boolean": type(value) is bool,
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
            "enum": value in shape.get("choices", []),
        }[kind]
        if not basic:
            return False
        if kind == "array" and shape.get("items"):
            return all(valid(v, shape["items"]) for v in value)
        if kind == "object":
            return all(
                k in value and valid(value[k], v) for k, v in shape.get("properties", {}).items()
            )
        return True

    for key, field in fields.items():
        import re

        pointer = field["binding"]
        if not pointer.startswith("/parameters/") or re.search(r"~(?:[^01]|$)", pointer):
            raise BusinessError("INVALID_CARD", "绑定必须指向 parameters 中的现有值")
        parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]

        def location(container, part):
            if isinstance(container, dict) and part in container:
                return part
            if (
                isinstance(container, list)
                and re.fullmatch(r"0|[1-9][0-9]*", part)
                and int(part) < len(container)
            ):
                return int(part)
            raise BusinessError("INVALID_CARD", "参数绑定不存在")

        current = result
        for part in parts[:-1]:
            current = current[location(current, part)]
        final = location(current, parts[-1])
        value = values.get(key, current[final])
        if not valid(value, field["shape"]):
            raise BusinessError("INVALID_CARD", "参数类型不符")
        current[final] = value
    if len(fields) != len(result["fields"]):
        raise BusinessError("INVALID_CARD", "参数标识重复")
    bindings = [f["binding"] for f in result["fields"]]
    if len(bindings) != len(set(bindings)):
        raise BusinessError("INVALID_CARD", "参数绑定重复")
    return result
