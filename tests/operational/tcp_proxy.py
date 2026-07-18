"""Task-local bounded TCP proxy used to simulate database network loss."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                return
            writer.write(chunk)
            await writer.drain()
    finally:
        writer.close()


async def main(host: str, upstream_port: int, listen_port: int, ready: Path) -> None:
    tasks: set[asyncio.Task] = set()

    async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(host, upstream_port), timeout=3
            )
        except Exception:
            writer.close()
            return
        pair = {
            asyncio.create_task(_pump(reader, upstream_writer)),
            asyncio.create_task(_pump(upstream_reader, writer)),
        }
        tasks.update(pair)
        try:
            await asyncio.wait(pair, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in pair:
                task.cancel()
                tasks.discard(task)

    server = await asyncio.start_server(accept, "127.0.0.1", listen_port)
    selected_port = int(server.sockets[0].getsockname()[1])
    ready.write_text(str(selected_port), encoding="ascii")
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stopped.set)
    try:
        await stopped.wait()
    finally:
        server.close()
        await server.wait_closed()
        for task in tuple(tasks):
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=2)
        ready.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4])))
