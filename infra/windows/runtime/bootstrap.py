"""Stable WSL entry point. No Windows drive mount or user shell is needed."""

import json
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path("/var/lib/trustrepro")
FILES = {"compose.yaml", "manifest.json", "control.py", "images.tar"}


def release():
    pointer = ROOT / "current"
    return Path(pointer.read_text().strip()) if pointer.exists() else None


def deploy(version, source):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?", version):
        raise ValueError("Invalid release version")
    current = release()
    if current:
        # An update must never interrupt an existing run, including exports.
        import subprocess

        subprocess.run([sys.executable, str(current / "control.py"), "idle"], check=True,
                       stdin=subprocess.DEVNULL)
    target = ROOT / "releases" / version
    stage = ROOT / "releases" / (version + ".pending")
    stage.mkdir(parents=True, exist_ok=True)
    seen = set()
    with tarfile.open(fileobj=source, mode="r|*") as archive:
        for member in archive:
            if member.name not in FILES or not member.isfile() or member.name in seen:
                raise ValueError("Unexpected file in release archive")
            seen.add(member.name)
            with archive.extractfile(member) as src, (stage / member.name).open("wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
    if seen != FILES:
        raise ValueError("Incomplete release archive")
    manifest = json.loads((stage / "manifest.json").read_text())
    if manifest["version"] != version or manifest["runtimeVersion"] != 1:
        raise ValueError("Incompatible release")
    if target.exists():
        # Do not overwrite a running release, even if its version label matches.
        if current == target:
            raise ValueError("Installed version has a different release identity")
        shutil.rmtree(target)
    stage.rename(target)
    # Keep the previous release until the new services become healthy.
    (ROOT / "candidate").write_text(str(target))
    print(json.dumps({"deployed": version}))


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if command == "probe":
        current = release()
        manifest = json.loads((current / "manifest.json").read_text()) if current else {}
        state = json.loads((ROOT / "state.json").read_text()) if (ROOT / "state.json").exists() else {}
        print(json.dumps({"runtimeVersion": 1, "release": manifest, "port": state.get("port")}))
    elif command == "deploy":
        deploy(sys.argv[2], sys.stdin.buffer)
    else:
        candidate = ROOT / "candidate"
        target = Path(candidate.read_text().strip()) if candidate.exists() else release()
        if not target:
            raise ValueError("Application release is not installed")
        os.execv(sys.executable, [sys.executable, str(target / "control.py"), *sys.argv[1:]])


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
