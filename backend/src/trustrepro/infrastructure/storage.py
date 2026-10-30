"""Scoped file descriptors and content-addressed artifacts; no tree checks on reads."""

import hashlib
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from trustrepro.domain.errors import BusinessError


def workspace_relative(value):
    if any(c in value for c in ("\\", ":", "\0")):
        raise BusinessError("INVALID_PATH", "路径必须位于当前工作区")
    if value.rstrip("/") == "/workspace":
        return "."
    value = value.removeprefix("/workspace/")
    if value.startswith("/"):
        raise BusinessError("INVALID_PATH", "路径必须位于当前工作区")
    parts = []
    for part in value.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise BusinessError("INVALID_PATH", "路径超出当前工作区")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts) or "."


def scoped_path(base: Path, relative: str):
    relative = workspace_relative(relative)
    path = PurePosixPath(relative)
    if (
        not relative
        or path.is_absolute()
        or "\\" in relative
        or ":" in relative
        or "\0" in relative
        or any(p in {"", ".", ".."} for p in relative.split("/"))
    ):
        raise BusinessError("INVALID_PATH", "路径必须位于当前工作区")
    target = base / relative
    if not target.resolve().is_relative_to(base.resolve()):
        raise BusinessError("INVALID_PATH", "路径超出当前工作区")
    return target


@contextmanager
def directory_fd(root, relative=".", *, parents=False):
    relative = workspace_relative(relative)
    if relative != ".":
        scoped_path(root, relative)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in [] if relative == "." else relative.split("/"):
            if parents:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def open_scoped(root, relative, mode="rb", *, parents=False):
    relative = workspace_relative(relative)
    target = scoped_path(root, relative)
    if os.name != "posix":
        if parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        with target.open(mode) as stream:
            yield stream
        return
    parent, name = relative.rsplit("/", 1) if "/" in relative else (".", relative)
    with directory_fd(root, parent, parents=parents) as descriptor:
        flags = os.O_RDONLY if "r" in mode else os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=descriptor)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise BusinessError("INVALID_PATH", "请选择普通文件")
        with os.fdopen(fd, mode, encoding=None if "b" in mode else "utf-8") as stream:
            yield stream


def list_scoped(root, relative, offset, limit):
    relative = workspace_relative(relative)
    if os.name == "posix":
        with directory_fd(root, relative) as fd, os.scandir(fd) as entries:
            values = [
                {"name": e.name, "directory": e.is_dir(follow_symlinks=False)} for e in entries
            ]
    else:
        target = root if relative == "." else scoped_path(root, relative)
        with os.scandir(target) as entries:
            values = [
                {"name": e.name, "directory": e.is_dir(follow_symlinks=False)} for e in entries
            ]
    values.sort(key=lambda e: (not e["directory"], e["name"]))
    return {
        "files": values[offset : offset + limit],
        "nextOffset": offset + limit if offset + limit < len(values) else None,
    }


def atomic_write(root, relative, content, checkpoint):
    raw = content.encode("utf-8")
    return atomic_copy(
        root,
        relative,
        (raw[i : i + 1024 * 1024] for i in range(0, len(raw), 1024 * 1024)),
        checkpoint,
    )


def atomic_copy(root, relative, chunks, checkpoint, *, mode=0o600):
    relative = workspace_relative(relative)
    scoped_path(root, relative)
    parent, name = relative.rsplit("/", 1) if "/" in relative else (".", relative)
    temporary = ".trustrepro-write-" + uuid.uuid4().hex
    with directory_fd(root, parent, parents=True) as fd:
        try:
            output = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            with os.fdopen(output, "wb") as stream:
                for chunk in chunks:
                    checkpoint()
                    stream.write(chunk)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            checkpoint()
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


@contextmanager
def workspace_lock(root, identifier, checkpoint):
    """The OS releases this per-workspace fence when a worker dies; no second state ledger."""
    import time

    directory = root / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / identifier).open("a") as stream:
        if os.name == "posix":
            import fcntl

            while True:
                checkpoint()
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.05)
        try:
            checkpoint()
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(stream, fcntl.LOCK_UN)


def object_path(root, sha):
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise BusinessError("INVALID_PATH", "产物标识无效")
    return root / "objects" / sha[:2] / sha


def ingest(root, source, operation, checkpoint=lambda: None, *, workspace=None, relative=None):
    temporary = root / "spool" / (operation + ".part")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    source_stream = open_scoped(workspace, relative) if workspace else source.open("rb")
    with source_stream as stream, temporary.open("wb") as out:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checkpoint()
            digest.update(chunk)
            out.write(chunk)
            size += len(chunk)
    sha = digest.hexdigest()
    target = object_path(root, sha)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        temporary.unlink(missing_ok=True)
    else:
        os.replace(temporary, target)
        target.chmod(0o444)
    return sha, size
