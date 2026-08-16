from __future__ import annotations

import asyncio

import uvicorn

from app.api import HttpAnalysisPublisher
from app.config import WorkerSettings
from app.health import create_health_app
from app.inference import DeterministicFakeAdapter, GeminiAdapter
from app.logging_config import configure_logging
from app.worker import VideoAnalysisWorker, WorkerRuntime


async def run() -> None:
    settings = WorkerSettings.from_env()
    configure_logging(settings.log_level)
    runtime = WorkerRuntime()
    if settings.ai_provider == "gemini":
        analyzer = GeminiAdapter(
            model=settings.gemini_model or "",
            api_key=settings.gemini_api_key,
            timeout_seconds=settings.gemini_timeout_seconds,
            max_retries=settings.gemini_max_retries,
        )
    else:
        analyzer = DeterministicFakeAdapter.from_files(
            fixture_path=settings.fake_fixture_path,
            scenario_path=settings.fake_scenario_path,
        )
    publisher = HttpAnalysisPublisher(
        base_url=settings.api_base_url,
        internal_token=settings.internal_token,
        timeout_seconds=settings.api_timeout_seconds,
    )
    worker = VideoAnalysisWorker(
        settings=settings,
        analyzer=analyzer,
        publisher=publisher,
        runtime=runtime,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_health_app(runtime),
            host=settings.health_host,
            port=settings.health_port,
            log_config=None,
            access_log=False,
        )
    )
    worker_task = asyncio.create_task(worker.run(), name="video-worker")
    server_task = asyncio.create_task(server.serve(), name="health-server")
    try:
        done, _ = await asyncio.wait(
            {worker_task, server_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception
    finally:
        worker.request_stop()
        server.should_exit = True
        await asyncio.gather(worker_task, server_task, return_exceptions=True)
        await publisher.close()


if __name__ == "__main__":
    asyncio.run(run())
