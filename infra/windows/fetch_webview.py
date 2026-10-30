"""Cache Microsoft's signed redistributable; bounded ranges tolerate proxy limits."""

import concurrent.futures
import json
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2] / "var/windows"
URL = "https://go.microsoft.com/fwlink/?linkid=2124701"
STEP = 4 * 1024 * 1024
DEST = ROOT / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    headers = ROOT / "webview.headers"
    response = subprocess.run([
        "curl.exe", "--fail", "--head", "--location", "--silent", "--show-error",
        "--connect-timeout", "15", "--max-time", "60", "--retry", "3",
        "--dump-header", str(headers), "--output", "NUL", "--write-out", "%{url_effective}", URL,
    ], check=True, capture_output=True, text=True)
    url = response.stdout.strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "msedge.sf.dl.delivery.mp.microsoft.com":
        raise ValueError("Unexpected Microsoft installer location")
    identifier = str(uuid.UUID(parsed.path.split("/")[-2]))
    size = int(re.findall(r"content-length:\s*(\d+)", headers.read_text(), re.I)[-1])
    metadata = ROOT / "webview-source.json"
    if metadata.exists() and DEST.exists():
        if json.loads(metadata.read_text()).get("url") == url and DEST.stat().st_size == size:
            return
    pieces = ROOT / "webview-parts" / identifier
    pieces.mkdir(parents=True, exist_ok=True)

    def fetch(index):
        start, end = index * STEP, min(size, (index + 1) * STEP) - 1
        part, header = pieces / f"{index:03}.bin", pieces / f"{index:03}.headers"
        expected = f"bytes {start}-{end}/{size}"

        def valid():
            ranges = re.findall(r"content-range:\s*(.*)", header.read_text(), re.I) if header.exists() else []
            return part.exists() and part.stat().st_size == end - start + 1 and ranges and ranges[-1].strip() == expected

        if not valid():
            subprocess.run([
                "curl.exe", "--fail", "--location", "--http1.1", "--range", f"{start}-{end}",
                "--retry", "3", "--connect-timeout", "15", "--max-time", "120",
                "--max-filesize", str(end - start + 1), "--silent", "--show-error",
                "--dump-header", str(header), url, "--output", str(part),
            ], check=True)
        if not valid():
            raise ValueError("Invalid Microsoft installer download range")
        return part

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(executor.map(fetch, range((size + STEP - 1) // STEP)))
    temp = DEST.with_suffix(".pending")
    with temp.open("wb") as target:
        for part in paths:
            with part.open("rb") as source:
                while data := source.read(1024 * 1024):
                    target.write(data)
    temp.replace(DEST)
    metadata.write_text(json.dumps({"url": url, "size": size}), encoding="utf-8")


if __name__ == "__main__":
    main()
