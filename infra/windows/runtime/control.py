"""Application-owned engine and Compose lifecycle inside the dedicated distro."""

import fcntl
import ipaddress
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("/var/lib/trustrepro")
RELEASE = Path(__file__).resolve().parent
CONFIG = ROOT / "runtime.env"
TOKEN = ROOT / "desktop-token"
STATE = ROOT / "state.json"


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def atomic(path, text):
    temp = path.with_suffix(".tmp")
    temp.write_text(text)
    temp.chmod(0o600)
    temp.replace(path)


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def compose(*args, **kwargs):
    return run([
        "docker", "compose", "--project-name", "trustrepro-desktop",
        "--env-file", str(CONFIG), "--file", str(RELEASE / "compose.yaml"), *args,
    ], **kwargs)


def request(path, body=None):
    port = state().get("port")
    if not port or not TOKEN.exists():
        return None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/desktop/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + TOKEN.read_text().strip(),
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)
    except urllib.error.URLError:
        # A stopped API does not establish that persisted work is idle.
        return None


def idle():
    value = request("status")
    if value is not None:
        if value["busy"]:
            raise RuntimeError("任务尚在运行，请完成或暂停后再更新")
        return
    if STATE.exists():
        run(["systemctl", "start", "docker"])
        compose("up", "-d", "--wait", "--wait-timeout", "120", "postgres")
        # Use the existing release's Python/ORM, without running migrations or models.
        compose("run", "--rm", "--no-deps", "api", "python", "-c",
                "from trustrepro.core.config import get_settings; "
                "from trustrepro.infrastructure.db.session import create_session_factory; "
                "from trustrepro.services.desktop import status; "
                "e,f=create_session_factory(get_settings()); "
                "s=f(); busy=status(s)['busy']; s.close(); e.dispose(); "
                "raise SystemExit(42 if busy else 0)")


def start(port):
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid application port")
    run(["systemctl", "start", "docker"])
    if not TOKEN.exists():
        atomic(TOKEN, secrets.token_urlsafe(48))
        os.chown(TOKEN, 10001, 10001)
    if not CONFIG.exists():
        atomic(CONFIG, "POSTGRES_PASSWORD=" + secrets.token_hex(32) + "\n")
    old = state()
    manifest = json.loads((RELEASE / "manifest.json").read_text())
    changed = old.get("version") != manifest["version"]
    if changed and old:
        # The outer controller already holds control.lock. Check directly;
        # launching another controller here would wait on our own lock.
        idle()
    password = next(line for line in CONFIG.read_text().splitlines()
                    if line.startswith("POSTGRES_PASSWORD="))
    routes = run(["ip", "-j", "-4", "route", "show", "default"],
                 stdout=subprocess.PIPE, text=True)
    gateways = [route["gateway"] for route in json.loads(routes.stdout) if "gateway" in route]
    windows_host = str(ipaddress.ip_address(gateways[0])) if gateways else "host-gateway"
    atomic(CONFIG, f"{password}\nTRUSTREPRO_HTTP_PORT={port}\n"
                  f"TRUSTREPRO_VERSION={manifest['version']}\n"
                  f"TRUSTREPRO_WINDOWS_HOST={windows_host}\n")
    os.environ["TRUSTREPRO_HTTP_PORT"] = str(port)
    os.environ["TRUSTREPRO_VERSION"] = manifest["version"]
    if old.get("imagesVersion") != manifest["version"]:
        run(["docker", "load", "--input", str(RELEASE / "images.tar")])
    for image in manifest["images"]:
        run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL)
    compose("run", "--rm", "--no-deps", "volume-init")
    compose("up", "-d", "--wait", "--wait-timeout", "120", "postgres")
    if changed and old:
        compose("stop", "-t", "60", "agent-runner", "job-worker", "progression",
                "sandbox-manager", "api")
        backup = ROOT / "backups"
        backup.mkdir(exist_ok=True)
        with (backup / f"{old['version']}-to-{manifest['version']}.dump").open("wb") as output:
            compose("exec", "-T", "postgres", "pg_dump", "-U", "trustrepro", "-Fc",
                    "trustrepro", stdout=output)
    # A failed migration leaves the old database/volumes intact; it is never reset.
    compose("run", "--rm", "--no-deps", "migrate")
    compose("up", "-d", "--wait", "--wait-timeout", "180",
            "api", "agent-runner", "job-worker", "progression", "sandbox-manager", "egress")
    atomic(STATE, json.dumps({"port": port, "version": manifest["version"],
                              "imagesVersion": manifest["version"]}))
    atomic(ROOT / "current", str(RELEASE))
    (ROOT / "candidate").unlink(missing_ok=True)


def stop(request_id):
    if not CONFIG.exists():
        return
    result = request("stop", {"requestId": request_id})
    if result is None:
        idle()
        run(["systemctl", "start", "docker"])
        compose("stop", "-t", "60")
        return
    for _ in range(120):
        result = request("status")
        if result is not None and not result["busy"]:
            compose("stop", "-t", "60")
            return
        time.sleep(1)
    raise RuntimeError("任务仍在停止，服务保持运行，请稍后重试")


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    # Serialize start/update/stop across installer and tray processes.
    with (ROOT / "control.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        command = sys.argv[1]
        if command == "start":
            start(int(sys.argv[2]))
        elif command == "idle":
            idle()
        elif command == "stop":
            stop(sys.argv[2])
        elif command == "endpoint":
            print(json.dumps({"port": state()["port"], "token": TOKEN.read_text().strip()}))
        else:
            raise ValueError("Unknown runtime command")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
