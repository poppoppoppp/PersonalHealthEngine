"""Deployment-only L7 entrypoint with the production pipeline consistency gate.

L1-L5 daily pipeline:
    pipeline.lock = EXCLUSIVE

L7 business requests:
    pipeline.lock = SHARED

Therefore L7 never reads L3-L5 while the daily pipeline is between stages.
The /health endpoint is deliberately excluded so container healthchecks remain responsive.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
from pathlib import Path

from l7.api.app import create_app as create_sealed_app


class PipelineGateMiddleware:
    def __init__(self, app, lock_path: Path):
        self.app = app
        self.lock_path = lock_path

    async def _acquire_shared(self, lock_file) -> None:
        while True:
            try:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_SH | fcntl.LOCK_NB,
                )
                return
            except BlockingIOError:
                # Never block the ASGI event loop.
                await asyncio.sleep(0.1)

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("path") == "/health"
        ):
            await self.app(scope, receive, send)
            return

        lock_file = self.lock_path.open("a+")

        try:
            await self._acquire_shared(lock_file)

            await self.app(
                scope,
                receive,
                send,
            )

        finally:
            try:
                fcntl.flock(
                    lock_file.fileno(),
                    fcntl.LOCK_UN,
                )
            finally:
                lock_file.close()


def create_app():
    runtime = os.environ.get("PHE_RUNTIME")

    if not runtime:
        raise RuntimeError(
            "PHE_RUNTIME must be set for production L7"
        )

    runtime_path = Path(runtime)
    runtime_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    lock_path = runtime_path / "pipeline.lock"

    sealed_app = create_sealed_app()

    return PipelineGateMiddleware(
        sealed_app,
        lock_path,
    )
