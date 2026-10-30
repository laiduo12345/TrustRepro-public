"""Background-only disk sampling; file reads never request a tree walk."""

import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiskUsage:
    total: int = 0
    directories: dict[str, int] = field(default_factory=dict)
    sampled_at: float = 0
    roots: tuple[str, ...] = ()


def measure_directories(roots: tuple[Path, ...]) -> DiskUsage:
    sizes: dict[str, int] = {}

    def visit(directory: Path) -> int:
        total = 0
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            total += visit(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except FileNotFoundError:
                        continue
        except FileNotFoundError:
            pass
        sizes[str(directory)] = total
        return total

    total = 0
    for root in roots:
        if root.is_symlink():
            continue
        total += visit(root)
    return DiskUsage(total, sizes, time.monotonic(), tuple(str(root) for root in roots))


class DiskUsageCache:
    def __init__(
        self,
        ttl_seconds: float = 10,
        measure: Callable[[tuple[Path, ...]], DiskUsage] = measure_directories,
    ):
        self.ttl = ttl_seconds
        self.measure = measure
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="disk-usage")
        self.lock = threading.RLock()
        self.samples: dict[str, DiskUsage] = {}
        self.pending: dict[str, Future] = {}
        self.generations: dict[str, int] = {}

    def _save(self, key: str, generation: int, future: Future) -> None:
        try:
            sample = future.result()
        except OSError as error:
            logger.warning("Disk sampling failed (%s)", type(error).__name__)
            sample = None
        with self.lock:
            if self.pending.get(key) is future:
                self.pending.pop(key, None)
            if sample is not None and self.generations.get(key) == generation:
                self.samples[key] = sample

    def read(self, key: str, roots: tuple[Path, ...]) -> DiskUsage:
        """Return the latest sample immediately; one background measurement per key."""
        with self.lock:
            sample = self.samples.get(key, DiskUsage())
            changed = sample.roots != tuple(str(root) for root in roots)
            if key not in self.pending and (
                changed or time.monotonic() - sample.sampled_at >= self.ttl
            ):
                generation = self.generations.get(key, 0) + 1
                self.generations[key] = generation
                future = self.pool.submit(self.measure, roots)
                self.pending[key] = future
                future.add_done_callback(lambda done: self._save(key, generation, done))
            return sample

    def close(self) -> None:
        self.pool.shutdown(wait=True)
