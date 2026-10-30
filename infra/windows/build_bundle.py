"""Produce self-contained WSL and application resources under var/windows."""

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var" / "windows"
BASE_IMAGES = [
    "public.ecr.aws/docker/library/busybox:1.37",
    "public.ecr.aws/docker/library/postgres:17@sha256:39fb82e41109483c81ac15422a302500b4a753777b47f8431038703536bc6c52",
]


def run(*args):
    return subprocess.run(args, check=True)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def build(version):
    bundle = OUT / "bundle"
    stage = OUT / "release"
    bundle.mkdir(parents=True, exist_ok=True)
    stage.mkdir(parents=True, exist_ok=True)
    images = [f"trustrepro-{name}:{version}" for name in ("backend", "sandbox", "egress")]
    for image in BASE_IMAGES:
        run("docker", "pull", image)
    # Docker save/load does not reliably restore registry RepoDigests. Give the
    # pinned PostgreSQL image a local tag so end users never need a registry pull.
    run("docker", "tag", BASE_IMAGES[1], "trustrepro-postgres:17-desktop")
    images.extend([BASE_IMAGES[0], "trustrepro-postgres:17-desktop"])
    run("docker", "save", "--output", str(stage / "images.tar"), *images)
    shutil.copyfile(ROOT / "infra/windows/compose.yaml", stage / "compose.yaml")
    shutil.copyfile(ROOT / "infra/windows/runtime/control.py", stage / "control.py")
    identity = hashlib.sha256()
    for name in ("images.tar", "compose.yaml", "control.py"):
        identity.update(digest(stage / name).encode())
    release_id = identity.hexdigest()
    manifest = {"version": version, "runtimeVersion": 1, "releaseId": release_id,
                "images": images, "sourceImages": BASE_IMAGES}
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with tarfile.open(bundle / "release.tar", "w") as archive:
        for name in ("compose.yaml", "manifest.json", "control.py", "images.tar"):
            archive.add(stage / name, arcname=name, recursive=False)
    container = "trustrepro-package-" + uuid.uuid4().hex[:12]
    try:
        run("docker", "create", "--name", container, "trustrepro-windows-runtime:1")
        run("docker", "export", "--output", str(OUT / "runtime.tar"), container)
        with (OUT / "runtime.tar").open("rb") as source:
            with gzip.open(bundle / "runtime.tar.gz", "wb", compresslevel=6) as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    finally:
        run("docker", "rm", container)
    (OUT / "runtime.tar").unlink()
    manifest = {**manifest, "files": {
        name: digest(bundle / name) for name in ("runtime.tar.gz", "release.tar")
    }}
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Windows runtime bundle: {bundle}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    build(parser.parse_args().version)
