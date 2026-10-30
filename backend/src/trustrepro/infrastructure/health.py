"""Process liveness files are probes, never a business state source."""

import json
import os
import sys
import time


class ServiceHealth:
    def __init__(self, settings, name):
        self.path = settings.var_root / "health" / (name + ".json")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def beat(self, ready=True):
        temporary = self.path.with_suffix(f".{os.getpid()}.part")
        temporary.write_text(json.dumps({"at": time.time(), "ready": ready}), encoding="utf-8")
        os.replace(temporary, self.path)

    def close(self):
        self.beat(False)

    def ready(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value["ready"] and time.time() - value["at"] < 20
        except (OSError, ValueError, KeyError):
            return False


if __name__ == "__main__":
    from trustrepro.core.config import get_settings

    sys.exit(0 if ServiceHealth(get_settings(), sys.argv[1]).ready() else 1)
