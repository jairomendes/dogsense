from __future__ import annotations

from fastapi import FastAPI, Response, status

from app.worker import WorkerRuntime


def create_health_app(runtime: WorkerRuntime) -> FastAPI:
    app = FastAPI(title="DogSense video worker", docs_url=None, redoc_url=None)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok" if runtime.running else "starting"}

    @app.get("/health/ready")
    async def ready(response: Response) -> dict[str, object]:
        if not runtime.ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "ready": runtime.ready,
            "monitoring_status": runtime.status.value,
            "last_frame_at": runtime.last_frame_at.isoformat()
            if runtime.last_frame_at is not None
            else None,
            "frames_received": runtime.frames_received,
            "frames_dropped": runtime.frames_dropped,
            "windows_scheduled": runtime.windows_scheduled,
            "windows_insufficient": runtime.windows_insufficient,
            "windows_dropped": runtime.windows_dropped,
            "inference_requests": runtime.inference_requests,
            "inference_errors": runtime.inference_errors,
            "invalidated_results": runtime.invalidated_results,
            "publications": runtime.publications,
            "publication_errors": runtime.publication_errors,
            "last_published_transition_seq": runtime.last_published_transition_seq,
            "inference_in_flight": runtime.inference_in_flight,
            "queue_depth": runtime.queue_depth,
        }

    return app
