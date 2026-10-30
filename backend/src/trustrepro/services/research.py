"""Read-only public research. Parsing happens in a bounded sandbox job."""

import asyncio
import hashlib
import json
import os
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import httpx

from trustrepro.domain.errors import BusinessError
from trustrepro.infrastructure.db.models import Source
from trustrepro.infrastructure.storage import atomic_copy, open_scoped
from trustrepro.infrastructure.text import visible_nul
from trustrepro.services import jobs


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def fetch(settings, url):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise BusinessError("INVALID_SOURCE", "来源必须是公开 HTTP(S) 地址")
    with httpx.Client(
        proxy=settings.egress_proxy or None, trust_env=False, timeout=45, follow_redirects=True
    ) as client:
        response = client.get(url, headers={"User-Agent": "TrustRepro research"})
        response.raise_for_status()
        return response.content, response.headers.get("content-type", ""), str(response.url)


async def read(factory, settings, binding, call_id, name, args):
    url = args.get("url", "")
    if name == "search_repositories":
        url = "https://api.github.com/search/repositories?q=" + quote(args["query"]) + "&per_page=8"
    if name == "read_paper":
        match = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", url)
        if match and "arxiv.org" in url:
            url = "https://arxiv.org/pdf/" + match[1]
    key = (
        url.removeprefix("upload:")
        if re.fullmatch(r"upload:[a-f0-9]{64}", url)
        else hashlib.sha256(url.encode()).hexdigest()
    )
    root = settings.var_root / "research" / binding.conversation_id
    root.mkdir(parents=True, exist_ok=True)
    cached = root / (key + ".body")
    if cached.is_file():
        with open_scoped(root, cached.name) as stream:
            body = stream.read()
        media, final = "", url
    else:
        body, media, final = await asyncio.to_thread(fetch, settings, url)
        atomic_copy(root, cached.name, [body], lambda: None)
    identifier = (
        "source-" + hashlib.sha256((binding.conversation_id + url).encode()).hexdigest()[:40]
    )
    source = {
        "id": identifier,
        "title": url,
        "url": final,
        "kind": "paper" if body.startswith(b"%PDF-") else "source",
    }
    with factory.begin() as db:
        if db.get(Source, identifier) is None:
            db.add(Source(id=identifier, conversation_id=binding.conversation_id, content=source))
    if body.startswith(b"%PDF-"):
        first, count = args.get("page_start", 1), args.get("page_count", 4)
        output = f"{key}-{first}-{count}.json"
        parsed = root / output
        if parsed.is_file():
            with open_scoped(root, output, "r") as stream:
                value = visible_nul(json.load(stream))
        else:
            with open_scoped(root, cached.name) as stream:
                os.fchmod(stream.fileno(), 0o444)
            with factory.begin() as db:
                job = jobs.enqueue(
                    db,
                    binding.account_id,
                    None,
                    "parse_pdf",
                    {
                        "conversationId": binding.conversation_id,
                        "file": cached.name,
                        "output": output,
                        "page_start": first,
                        "page_count": count,
                    },
                    f"parse:{binding.id}:{call_id}",
                    scope="research",
                    session_id=binding.id,
                )
            result = await jobs.wait(factory, job.id)
            if result["status"] != "COMPLETED":
                return {"error": result["error"], "sources": [source]}
            value = result["result"]["paper"]
        return {
            **value,
            "sources": [source],
            "nextPage": first + count,
            "hasMore": first + count <= value["pageCount"],
        }
    text = body.decode("utf-8", errors="replace")
    if "<html" in text[:3000].lower() or "text/html" in media:
        parser = Text()
        parser.feed(text)
        text = "".join(parser.parts)
    if name == "search_repositories":
        value = json.loads(text)
        return {
            "repositories": [
                {"name": r["full_name"], "url": r["html_url"], "description": r.get("description")}
                for r in value.get("items", [])
            ],
            "sources": [source],
        }
    # Notebook output is unnecessary context; keep code and markdown cells.
    if url.split("?")[0].endswith(".ipynb"):
        notebook = json.loads(text)
        text = "\n".join("".join(c.get("source", [])) for c in notebook.get("cells", []))
    lines = text.splitlines()
    start, count = args.get("start_line", 1), args.get("line_count", 100)
    selected = lines[start - 1 : start - 1 + count]
    return {
        "text": "\n".join(selected),
        "startLine": start,
        "nextLine": start + len(selected),
        "hasMore": start + len(selected) <= len(lines),
        "sources": [source],
    }
