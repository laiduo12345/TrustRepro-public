"""Linux agent-runner entry point."""

import asyncio
import logging

from trustrepro.infrastructure.codex.runner import main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
