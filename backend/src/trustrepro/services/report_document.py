"""Readable report exports from the same ordered blocks used by the desktop UI."""

import json
import re
from pathlib import PurePosixPath
from urllib.parse import quote

REPORT_GUIDANCE = """报告写作指引（结构建议，不是科研执行阶段或提交验收关卡）：
报告不写摘要。推荐从实验目标与复现范围开始，随后说明方法与实验设置、实验结果、
分析与讨论、结论与局限、参考文献及复跑说明；必要细节放附录。
按实验规模自主合并、调整或省略不适用章节，不必机械填满，不为补章节增加实验或审核。
正文应自足，说明理解结果所需的数据、主要参数、重复次数、指标及环境；区分原论文事实、
实际观察和解释。科学检查按用户任务完成，工程日志和逐文件记录仅按需要引用。
result.title可填写具体报告标题；result.blocks按正文顺序提交完整报告，而非只交完成摘要、
把完整报告藏在另一个附件中。summary为可选聊天卡片短说明，不是报告正文，无需生成摘要。
文本块格式为{type:"text",title:"章节标题",data:{text:"Markdown正文"}}，title可省略。
图表使用image/table块，按出现顺序分别自动编号；title填写简短标题（无需编号），
data.caption填写图注/表注。图片用data.artifactId；表格用data.columns和data.rows。
需要正文引用时给图表设置唯一id（如fig-main、table-results，仅字母数字及连字符，字母开头），
正文用[图1](#fig-main)、[表1](#table-results)。不指定id时自动使用figure-1、table-1等。
文件块使用data.artifactId，正文文件链接用[名称](artifact:已登记产物ID)，不要使用宿主路径。
图表说明写明读图所需的条件、单位和误差含义；是否需要图表由实验内容决定。
平台会从同一份blocks生成网页、Markdown和产物包，不必另写一份内容不同的完整报告，不生成PDF。
"""

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
ANCHOR = re.compile(r"[A-Za-z][A-Za-z0-9-]*\Z")


def archive_path(artifact):
    # Each registered artifact has its own directory; keep normal workspace paths.
    name = str(artifact["name"]).replace("\\", "/")
    parts = [p for p in PurePosixPath(name).parts if p not in {"/", ".", ".."}]
    return "artifacts/" + artifact["id"] + "/" + "/".join(parts)


def block_layout(blocks):
    used = set()
    counters = {"image": 0, "table": 0}
    # Reserve explicit IDs so a fallback cannot steal a later reference.
    explicit = {
        b["id"]
        for b in blocks
        if isinstance(b, dict) and isinstance(b.get("id"), str) and ANCHOR.fullmatch(b["id"])
    }
    for index, block in enumerate(blocks):
        value = block if isinstance(block, dict) else {}
        kind = value.get("type") if isinstance(value.get("type"), str) else None
        number = None
        if kind in counters:
            counters[kind] += 1
            number = counters[kind]
        prefix = {"image": "figure", "table": "table"}.get(kind, "section")
        fallback = f"{prefix}-{number if number is not None else index + 1}"
        identifier = value.get("id")
        if (
            not isinstance(identifier, str)
            or not ANCHOR.fullmatch(identifier)
            or identifier in used
        ):
            identifier = fallback
            suffix = 2
            while identifier in used or identifier in explicit:
                identifier = f"{fallback}-{suffix}"
                suffix += 1
        used.add(identifier)
        title = str(value.get("title") or "")
        if number is not None:
            label = "图" if kind == "image" else "表"
            # Existing reports may already carry a figure/table number.
            if not re.match(rf"^{label}\s*\d|^(?:Figure|Table)\s+\d", title, re.I):
                title = f"{label} {number}" + (f" · {title}" if title else "")
        yield block, identifier, title


def code_block(value):
    body = json.dumps(value, ensure_ascii=False, indent=2)
    fence = "`" * (max([2, *[len(s) for s in re.findall(r"`+", body)]]) + 1)
    return f"{fence}json\n{body}\n{fence}"


def cell(value):
    text = (
        ""
        if value is None
        else (
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list, bool))
            else str(value)
        )
    )
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", "")
        .replace("\n", "<br>")
    )


def table_markdown(data):
    obj = data if isinstance(data, dict) else {}
    rows = data if isinstance(data, list) else obj.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return code_block(data)
    columns = obj.get("columns")
    if not isinstance(columns, list):
        columns = list(dict.fromkeys(k for row in rows if isinstance(row, dict) for k in row))
    if not columns:
        return code_block(data)
    keys, labels = [], []
    for index, column in enumerate(columns):
        if isinstance(column, dict):
            key = column.get("key", column.get("dataIndex", str(index)))
            label = column.get("title", column.get("label", key))
        else:
            key = label = column
        keys.append(str(key))
        labels.append(cell(label))
    lines = ["| " + " | ".join(labels) + " |", "| " + " | ".join("---" for _ in keys) + " |"]
    for row in rows:
        values = [
            row.get(key)
            if isinstance(row, dict)
            else (row[i] if isinstance(row, list) and i < len(row) else None)
            for i, key in enumerate(keys)
        ]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    return "\n".join(lines)


def artifact_links(text, artifacts):
    # Only rewrite the documented artifact-link syntax, leaving code examples intact.
    pattern = r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*`+)|\((?:artifact:)?(artifact-[\w-]+)\)"

    def replace(match):
        if match[1]:
            return match[0]
        artifact = artifacts.get(match[2])
        return "(" + quote(archive_path(artifact), safe="/") + ")" if artifact else match[0]

    return re.sub(pattern, replace, text)


def markdown(value):
    result = value["result"]
    artifacts = {a["id"]: a for a in value.get("artifacts", [])}
    blocks = result.get("blocks", [])
    blocks = blocks if isinstance(blocks, list) else []
    lines = ["# " + str(result.get("title") or "实验报告"), ""]
    extras = {k: v for k, v in result.items() if k not in {"title", "summary", "blocks"}}
    block_extras = []
    for block, identifier, title in block_layout(blocks):
        lines += [f'<a id="{identifier}"></a>', ""]
        if title:
            lines += ["## " + title, ""]
        if not isinstance(block, dict):
            lines += [code_block(block), ""]
            continue
        data = block.get("data")
        obj = data if isinstance(data, dict) else {}
        text = obj.get("text") if obj else data
        artifact = artifacts.get(str(obj.get("artifactId", "")))
        kind = block.get("type") if isinstance(block.get("type"), str) else None
        consumed = set()
        if kind == "text" and isinstance(text, str):
            lines.append(artifact_links(text, artifacts))
            consumed = {"text"}
        elif kind == "table":
            lines.append(table_markdown(data))
            consumed = {"columns", "rows"}
        elif kind in {"image", "file"} and artifact:
            label = str(obj.get("label") or obj.get("title") or title or artifact["name"])
            label = re.sub(r"([\\\[\]])", r"\\\1", label).replace("\n", " ")
            image = kind == "image" and artifact["mediaType"] in IMAGE_TYPES
            lines.append(
                ("!" if image else "") + f"[{label}]({quote(archive_path(artifact), safe='/')})"
            )
            consumed = {"artifactId", "label", "title"}
        else:
            lines.append(code_block(block))
            lines.append("")
            continue
        if isinstance(obj.get("caption"), str):
            lines += ["", artifact_links(obj["caption"], artifacts)]
            consumed.add("caption")
        extra = {k: v for k, v in block.items() if k not in {"id", "title", "type", "data"}}
        extra_data = {k: v for k, v in obj.items() if k not in consumed}
        if extra_data:
            extra["data"] = extra_data
        if extra:
            block_extras.append({"blockId": identifier, **extra})
        lines.append("")
    if extras or block_extras:
        lines += ["## 附加数据", "", code_block({"fields": extras, "blocks": block_extras}), ""]
    lines += ["[完整原始数据与运行记录](report.json)", ""]
    return "\n".join(lines).encode("utf-8")
