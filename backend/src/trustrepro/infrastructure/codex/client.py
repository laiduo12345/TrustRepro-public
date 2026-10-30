"""Pinned app-server stdio JSON-RPC transport. Business state never enters Codex storage."""

import asyncio
import json
from pathlib import Path

import jsonschema


class AppServer:
    def __init__(self, binary, env, cwd, diagnostics):
        self.binary, self.env, self.cwd = binary, env, cwd
        self.diagnostics = diagnostics
        self.sequence = 0
        self.pending = {}
        self.events = asyncio.Queue()
        self.requests = asyncio.Queue()
        self.write_lock = asyncio.Lock()

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            self.binary,
            "app-server",
            "--listen",
            "stdio://",
            env=self.env,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._stderr())
        await self.request(
            "initialize",
            {
                "clientInfo": {"name": "trustrepro", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.send({"method": "initialized"})

    async def _stderr(self):
        with Path(self.diagnostics).open("ab") as output:
            while line := await self.process.stderr.readline():
                output.write(line)
                output.flush()

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message:
                    if "id" in message:
                        if message["method"] == "item/tool/call":
                            schema = json.loads(
                                (
                                    Path(__file__).parent / "schema" / "DynamicToolCallParams.json"
                                ).read_text()
                            )
                            jsonschema.validate(message["params"], schema)
                        elif message["method"] == "item/tool/requestUserInput":
                            schema = json.loads(
                                (
                                    Path(__file__).parent
                                    / "schema"
                                    / "ToolRequestUserInputParams.json"
                                ).read_text()
                            )
                            jsonschema.validate(message["params"], schema)
                        await self.requests.put(message)
                    else:
                        await self.events.put(message)
                elif message.get("id") in self.pending:
                    future = self.pending.pop(message["id"])
                    if not future.done():
                        if "error" in message:
                            future.set_exception(
                                RuntimeError(json.dumps(message["error"], ensure_ascii=False))
                            )
                        else:
                            future.set_result(message.get("result"))
        finally:
            error = RuntimeError("Codex app-server connection closed")
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            await self.events.put({"method": "transport/closed"})

    async def send(self, data):
        async with self.write_lock:
            self.process.stdin.write((json.dumps(data, ensure_ascii=False) + "\n").encode())
            await self.process.stdin.drain()

    async def request(self, method, params):
        schema_name = {
            "thread/start": "ThreadStartParams",
            "thread/resume": "ThreadResumeParams",
            "turn/start": "TurnStartParams",
            "turn/interrupt": "TurnInterruptParams",
            "turn/steer": "TurnSteerParams",
            "thread/read": "ThreadReadParams",
            "thread/turns/list": "ThreadTurnsListParams",
            "thread/items/list": "ThreadItemsListParams",
        }.get(method)
        if schema_name:
            schema = json.loads(
                (Path(__file__).parent / "schema" / (schema_name + ".json")).read_text()
            )
            jsonschema.validate(params, schema)
        self.sequence += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[self.sequence] = future
        await self.send({"id": self.sequence, "method": method, "params": params})
        return await asyncio.wait_for(future, 60)

    async def close(self):
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        await asyncio.gather(self.reader, self.stderr, return_exceptions=True)
