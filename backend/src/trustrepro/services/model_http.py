"""Resolve desktop-local model addresses only at the outgoing HTTP boundary."""

import httpx

from trustrepro.core.config import Settings


def request_options(settings: Settings, url: str, headers: dict[str, str]) -> dict:
    target = httpx.URL(url)
    if not settings.desktop_windows_loopback or target.host not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        return {"url": url, "headers": headers}
    # The Windows runtime maps this alias to the Windows-side WSL gateway.
    # Keep the original HTTP authority and TLS identity for local virtual hosts
    # and certificates; never disable certificate verification or rewrite storage.
    return {
        "url": str(target.copy_with(host="host.docker.internal")),
        "headers": {**headers, "Host": target.netloc.decode("ascii")},
        "extensions": {"sni_hostname": target.host},
    }
