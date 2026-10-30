"""Trusted PDF parser entrypoint; invoked only in a networkless bounded container."""

import json
import sys
from pathlib import Path

import pymupdf


def main() -> None:
    source, target = map(Path, sys.argv[1:3])
    with pymupdf.open(source) as document:
        if document.needs_pass:
            raise ValueError("PDF 已加密")
        start = max(1, int(sys.argv[3])) if len(sys.argv) > 3 else 1
        count = int(sys.argv[4]) if len(sys.argv) > 4 else 8
        pages = []
        for index in range(start - 1, min(document.page_count, start - 1 + count)):
            page = document[index]
            tables = []
            try:
                for table in page.find_tables().tables:
                    tables.append(table.extract())
            except (ValueError, RuntimeError):
                pass
            pages.append({"page": index + 1, "text": page.get_text(), "tables": tables})
        result = {
            "title": document.metadata.get("title", ""),
            "pages": pages,
            "pageCount": document.page_count,
        }
    target.write_text(json.dumps(result, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    main()
