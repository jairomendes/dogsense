from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import secrets
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.config import Settings
from app.models import CameraTestResult, IntegrationReport, IntegrationStatus, StateEvent, utcnow


class CameraProbe(Protocol):
    async def probe(self, url: str, username: str | None, password: str | None) -> CameraTestResult: ...


class StreamPublisher(Protocol):
    async def publish(self, path: str, source_url: str) -> None: ...

    async def remove(self, path: str) -> None: ...


class SpeechProvider(Protocol):
    async def synthesize(self, text: str, language: str) -> tuple[bytes, str]: ...

    async def health(self) -> IntegrationReport: ...


class ReceiptLedger(Protocol):
    async def publish_memo(self, memo: str) -> tuple[str, bool]: ...

    async def fetch_memo(self, transaction_signature: str) -> str | None: ...

    async def health(self) -> IntegrationReport: ...


class AnalyticsSink(Protocol):
    async def sync_event(self, event: StateEvent) -> None: ...

    async def health(self) -> IntegrationReport: ...


@dataclass(slots=True)
class IntegrationBundle:
    camera: CameraProbe
    stream: StreamPublisher
    speech: SpeechProvider
    ledger: ReceiptLedger
    analytics: AnalyticsSink


class FakeCameraProbe:
    async def probe(self, url: str, username: str | None, password: str | None) -> CameraTestResult:
        await asyncio.sleep(0)
        return CameraTestResult(
            success=True,
            camera_id="pending",
            frames_received=5,
            codec="h264",
            resolution="1280x720",
            fps=24.0,
            first_frame_ms=42,
            preview_available=False,
            message="Demo probe received five valid frames",
            checked_at=utcnow(),
        )


def _credential_url(url: str, username: str | None, password: str | None) -> str:
    if not username and not password:
        return url
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    credentials = quote(username or "", safe="")
    if password is not None:
        credentials += ":" + quote(password, safe="")
    netloc = f"{credentials}@{host}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


class FFprobeCameraProbe:
    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds

    async def probe(self, url: str, username: str | None, password: str | None) -> CameraTestResult:
        started = time.perf_counter()
        stream_url = _credential_url(url, username, password)
        command = (
            "ffprobe",
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-read_intervals",
            "%+4",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,nb_read_frames",
            "-of",
            "json",
            stream_url,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except FileNotFoundError:
            return CameraTestResult(
                success=False,
                camera_id="pending",
                frames_received=0,
                message="ffprobe is not installed",
                checked_at=utcnow(),
            )
        except TimeoutError:
            if "process" in locals():
                process.kill()
                await process.communicate()
            return CameraTestResult(
                success=False,
                camera_id="pending",
                frames_received=0,
                message="camera probe timed out",
                checked_at=utcnow(),
            )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            payload = json.loads(stdout)
            stream = payload.get("streams", [])[0]
            frames = int(stream.get("nb_read_frames") or 0)
            numerator, denominator = (stream.get("r_frame_rate") or "0/1").split("/", 1)
            fps = float(numerator) / max(float(denominator), 1.0)
        except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
            stream, frames, fps = {}, 0, 0.0
        success = process.returncode == 0 and frames >= 5
        return CameraTestResult(
            success=success,
            camera_id="pending",
            frames_received=frames,
            codec=stream.get("codec_name"),
            resolution=f"{stream.get('width')}x{stream.get('height')}" if stream.get("width") else None,
            fps=fps,
            first_frame_ms=elapsed_ms,
            preview_available=False,
            message="Camera connection validated" if success else "Fewer than five valid frames were received",
            checked_at=utcnow(),
        )


class FakeStreamPublisher:
    async def publish(self, path: str, source_url: str) -> None:
        return None

    async def remove(self, path: str) -> None:
        return None


class MediaMTXPublisher:
    def __init__(self, api_url: str):
        self.api_url = api_url

    async def publish(self, path: str, source_url: str) -> None:
        body = {"source": source_url, "sourceOnDemand": False, "record": False}
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.patch(f"{self.api_url}/v3/config/paths/patch/{path}", json=body)
            if response.status_code == 404:
                response = await client.post(f"{self.api_url}/v3/config/paths/add/{path}", json=body)
            response.raise_for_status()

    async def remove(self, path: str) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.delete(f"{self.api_url}/v3/config/paths/delete/{path}")
            if response.status_code not in {200, 204, 404}:
                response.raise_for_status()


def _fake_wav() -> bytes:
    output = io.BytesIO()
    sample_rate = 8_000
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        samples = bytearray()
        for index in range(sample_rate // 5):
            value = int(2_500 * math.sin(2 * math.pi * 440 * index / sample_rate))
            samples.extend(value.to_bytes(2, "little", signed=True))
        wav.writeframes(bytes(samples))
    return output.getvalue()


class FakeSpeechProvider:
    async def synthesize(self, text: str, language: str) -> tuple[bytes, str]:
        await asyncio.sleep(0)
        return _fake_wav(), "audio/wav"

    async def health(self) -> IntegrationReport:
        return IntegrationReport(
            name="elevenlabs",
            mode="fake",
            status=IntegrationStatus.available,
            configured=True,
            detail="deterministic fake",
        )


class ElevenLabsSpeechProvider:
    def __init__(self, api_key: str | None, voice_id: str, model_id: str):
        self.api_key, self.voice_id, self.model_id = api_key, voice_id, model_id

    async def synthesize(self, text: str, language: str) -> tuple[bytes, str]:
        if not self.api_key or not self.voice_id:
            raise RuntimeError("ElevenLabs credentials are not configured")
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{quote(self.voice_id, safe='')}",
                headers={"xi-api-key": self.api_key, "accept": "audio/mpeg"},
                json={"text": text, "model_id": self.model_id},
            )
            response.raise_for_status()
            return response.content, response.headers.get("content-type", "audio/mpeg").split(";", 1)[0]

    async def health(self) -> IntegrationReport:
        if not self.api_key:
            return IntegrationReport(
                name="elevenlabs", mode="real", status=IntegrationStatus.not_configured, configured=False
            )
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": self.api_key})
                response.raise_for_status()
            return IntegrationReport(
                name="elevenlabs",
                mode="real",
                status=IntegrationStatus.available,
                configured=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception:
            return IntegrationReport(
                name="elevenlabs",
                mode="real",
                status=IntegrationStatus.degraded,
                configured=True,
                detail="health check failed",
            )


class FakeReceiptLedger:
    def __init__(self):
        self.memos: dict[str, str] = {}

    async def publish_memo(self, memo: str) -> tuple[str, bool]:
        signature = "demo-" + secrets.token_hex(32)
        self.memos[signature] = memo
        return signature, True

    async def fetch_memo(self, transaction_signature: str) -> str | None:
        return self.memos.get(transaction_signature)

    async def health(self) -> IntegrationReport:
        return IntegrationReport(
            name="solana",
            mode="fake",
            status=IntegrationStatus.available,
            configured=True,
            detail="deterministic devnet fake",
        )


class SolanaRpcLedger:
    MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"

    def __init__(self, rpc_url: str, keypair_path: str | None):
        self.rpc_url, self.keypair_path = rpc_url, keypair_path

    def _keypair(self) -> Any:
        if not self.keypair_path:
            raise RuntimeError("SOLANA_KEYPAIR_PATH is not configured")
        try:
            from solders.keypair import Keypair
        except ImportError as exc:
            raise RuntimeError("solders is required for SOLANA_MODE=real") from exc
        values = json.loads(Path(self.keypair_path).read_text(encoding="utf-8"))
        return Keypair.from_bytes(bytes(values))

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                self.rpc_url,
                json={"jsonrpc": "2.0", "id": secrets.randbelow(1_000_000), "method": method, "params": params},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("error"):
            raise RuntimeError(f"Solana RPC error {payload['error'].get('code')}")
        return payload.get("result")

    async def publish_memo(self, memo: str) -> tuple[str, bool]:
        try:
            from solders.hash import Hash
            from solders.instruction import Instruction
            from solders.message import Message
            from solders.pubkey import Pubkey
            from solders.transaction import Transaction
        except ImportError as exc:
            raise RuntimeError("solders is required for SOLANA_MODE=real") from exc
        payer = self._keypair()
        latest = await self._rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
        blockhash = Hash.from_string(latest["value"]["blockhash"])
        instruction = Instruction(Pubkey.from_string(self.MEMO_PROGRAM), memo.encode("utf-8"), [])
        message = Message.new_with_blockhash([instruction], payer.pubkey(), blockhash)
        transaction = Transaction.new_unsigned(message)
        transaction.sign([payer], blockhash)
        encoded = base64.b64encode(bytes(transaction)).decode("ascii")
        signature = await self._rpc(
            "sendTransaction", [encoded, {"encoding": "base64", "preflightCommitment": "confirmed"}]
        )
        return str(signature), True

    async def fetch_memo(self, transaction_signature: str) -> str | None:
        result = await self._rpc(
            "getTransaction", [transaction_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        )
        if not result:
            return None
        instructions = result.get("transaction", {}).get("message", {}).get("instructions", [])
        for instruction in instructions:
            if instruction.get("program") == "spl-memo" and isinstance(instruction.get("parsed"), str):
                return instruction["parsed"]
        return None

    async def health(self) -> IntegrationReport:
        if not self.keypair_path:
            return IntegrationReport(
                name="solana",
                mode="real",
                status=IntegrationStatus.not_configured,
                configured=False,
            )
        started = time.perf_counter()
        try:
            await self._rpc("getHealth", [])
            return IntegrationReport(
                name="solana",
                mode="real",
                status=IntegrationStatus.available,
                configured=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception:
            return IntegrationReport(
                name="solana",
                mode="real",
                status=IntegrationStatus.degraded,
                configured=True,
                detail="health check failed",
            )


class FakeAnalyticsSink:
    async def sync_event(self, event: StateEvent) -> None:
        await asyncio.sleep(0)

    async def health(self) -> IntegrationReport:
        return IntegrationReport(
            name="snowflake",
            mode="fake",
            status=IntegrationStatus.available,
            configured=True,
            detail="deterministic fake",
        )


class SnowflakeAnalyticsSink:
    def __init__(self):
        self.options = {
            "account": os.getenv("SNOWFLAKE_ACCOUNT"),
            "user": os.getenv("SNOWFLAKE_USER"),
            "database": os.getenv("SNOWFLAKE_DATABASE"),
            "schema": os.getenv("SNOWFLAKE_SCHEMA"),
            "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
            "password": os.getenv("SNOWFLAKE_PASSWORD"),
            "private_key_file": os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH"),
            "private_key_file_pwd": os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        }

    @property
    def configured(self) -> bool:
        return all(self.options.get(key) for key in ("account", "user", "database", "schema", "warehouse"))

    def _connect(self) -> Any:
        if not self.configured:
            raise RuntimeError("Snowflake credentials are not configured")
        try:
            import snowflake.connector
        except ImportError as exc:
            raise RuntimeError("snowflake-connector-python is required for SNOWFLAKE_MODE=real") from exc
        return snowflake.connector.connect(**{key: value for key, value in self.options.items() if value})

    async def sync_event(self, event: StateEvent) -> None:
        def execute() -> None:
            connection = self._connect()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    MERGE INTO STATE_EVENTS target
                    USING (SELECT %s EVENT_ID, %s STARTED_AT, %s ENDED_AT, %s ACTIVITY, %s STATE,
                                  %s CONFIDENCE_AVG, %s PROMPT_VERSION, %s MODEL_NAME) source
                    ON target.EVENT_ID = source.EVENT_ID
                    WHEN MATCHED THEN UPDATE SET ENDED_AT=source.ENDED_AT, CONFIDENCE_AVG=source.CONFIDENCE_AVG
                    WHEN NOT MATCHED THEN INSERT
                      (EVENT_ID, STARTED_AT, ENDED_AT, ACTIVITY, STATE, CONFIDENCE_AVG, PROMPT_VERSION, MODEL_NAME)
                    VALUES
                      (source.EVENT_ID, source.STARTED_AT, source.ENDED_AT, source.ACTIVITY, source.STATE,
                       source.CONFIDENCE_AVG, source.PROMPT_VERSION, source.MODEL_NAME)
                    """,
                    (
                        event.id,
                        event.started_at,
                        event.ended_at,
                        getattr(event.activity, "value", event.activity),
                        getattr(event.state, "value", event.state),
                        event.confidence_avg,
                        event.prompt_version,
                        event.model_name,
                    ),
                )
                connection.commit()
            finally:
                connection.close()

        await asyncio.to_thread(execute)

    async def health(self) -> IntegrationReport:
        if not self.configured:
            return IntegrationReport(
                name="snowflake",
                mode="real",
                status=IntegrationStatus.not_configured,
                configured=False,
            )
        started = time.perf_counter()
        try:
            await asyncio.to_thread(lambda: self._connect().close())
            return IntegrationReport(
                name="snowflake",
                mode="real",
                status=IntegrationStatus.available,
                configured=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception:
            return IntegrationReport(
                name="snowflake",
                mode="real",
                status=IntegrationStatus.degraded,
                configured=True,
                detail="health check failed",
            )


class AudioStorage:
    def __init__(self, directory: Path):
        self.directory = directory

    async def initialize(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, speech_id: str) -> Path:
        if not speech_id.replace("-", "").isalnum():
            raise ValueError("invalid speech id")
        return self.directory / f"{speech_id}.audio"

    async def write(self, speech_id: str, content: bytes) -> None:
        destination = self.path(speech_id)
        await asyncio.to_thread(destination.write_bytes, content)
        destination.chmod(0o600)

    async def read(self, speech_id: str) -> bytes:
        return await asyncio.to_thread(self.path(speech_id).read_bytes)

    async def delete(self, speech_id: str) -> None:
        path = self.path(speech_id)
        if path.exists():
            await asyncio.to_thread(path.unlink)


def build_integrations(settings: Settings) -> IntegrationBundle:
    camera: CameraProbe = FakeCameraProbe() if settings.camera_adapter == "fake" else FFprobeCameraProbe()
    stream: StreamPublisher = (
        FakeStreamPublisher() if settings.mediamtx_mode == "fake" else MediaMTXPublisher(settings.mediamtx_api_url)
    )
    speech: SpeechProvider = (
        FakeSpeechProvider()
        if settings.elevenlabs_mode == "fake"
        else ElevenLabsSpeechProvider(
            settings.elevenlabs_api_key,
            settings.elevenlabs_voice_id,
            settings.elevenlabs_model_id,
        )
    )
    ledger: ReceiptLedger = (
        FakeReceiptLedger()
        if settings.solana_mode == "fake"
        else SolanaRpcLedger(settings.solana_rpc_url, settings.solana_keypair_path)
    )
    analytics: AnalyticsSink = FakeAnalyticsSink() if settings.snowflake_mode == "fake" else SnowflakeAnalyticsSink()
    return IntegrationBundle(camera=camera, stream=stream, speech=speech, ledger=ledger, analytics=analytics)
