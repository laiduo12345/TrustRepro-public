"""Container-local command wrapper; receipts survive a Manager disconnect."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main():
    identifier, command, cwd = sys.argv[1:4]
    root = Path("/workspace/.trustrepro") / identifier
    root.mkdir(parents=True, exist_ok=True)
    started = root / "started"
    if command == "--cancel":
        if (root / "pid").exists():
            try:
                os.killpg(int((root / "pid").read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
        return
    try:
        fd = os.open(started, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # A durable operation is never launched a second time.
        return
    os.close(fd)
    begin = time.time()
    with (root / "stdout").open("wb") as out, (root / "stderr").open("wb") as err:
        process = subprocess.Popen(
            ["/bin/bash", "-c", command], cwd=cwd, stdout=out, stderr=err, start_new_session=True
        )
        (root / "pid").write_text(str(process.pid))
        code = process.wait()
    result = {"exitCode": code, "seconds": time.time() - begin}
    temporary = root / "result.part"
    temporary.write_text(json.dumps(result))
    temporary.replace(root / "result.json")
    sys.exit(code if code >= 0 else 128 - code)


if __name__ == "__main__":
    main()
