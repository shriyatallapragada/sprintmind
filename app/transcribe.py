"""Speech-to-text for recorded meetings, using free Whisper backends.

  * GroqWhisper  - whisper-large-v3-turbo on Groq's free tier. Reuses GROQ_API_KEY, fast, no GPU.
  * LocalWhisper - faster-whisper running on this machine. Fully offline, nothing leaves the laptop.

Whisper does not label speakers, so the transcript is timestamped but speaker-less. The meeting
summariser only assigns an owner when the words themselves make it clear ("Priya, can you...").
"""
from __future__ import annotations

import asyncio
import io
from typing import Any, Protocol

from .inspector import Inspector


class TranscriptionError(RuntimeError):
    pass


class Transcriber(Protocol):
    engine: str

    async def transcribe(self, audio: bytes, filename: str, *, language: str | None = None,
                         prompt: str | None = None) -> dict[str, Any]: ...


def format_timestamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def segments_to_text(segments: list[dict[str, Any]]) -> str:
    """One timestamped line per segment, the same shape as a Teams transcript export."""
    return "\n".join(f"{format_timestamp(s['start'])} {s['text'].strip()}" for s in segments if s["text"].strip())


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


class GroqWhisper:
    engine = "groq-whisper"

    def __init__(self, api_key: str | None, base_url: str, model: str, timeout: float, inspector: Inspector) -> None:
        from openai import AsyncOpenAI

        if not api_key:
            raise TranscriptionError("GROQ_API_KEY is not set")
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=max(timeout, 300), max_retries=2)
        self.model = model
        self.inspector = inspector

    async def transcribe(self, audio: bytes, filename: str, *, language: str | None = None,
                         prompt: str | None = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.model, "file": (filename, audio),
                                  "response_format": "verbose_json", "temperature": 0.0}
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt
        async with self.inspector.track("transcribe", engine=self.engine, model=self.model, filename=filename,
                                        bytes=len(audio)) as out:
            resp = await self.client.audio.transcriptions.create(**kwargs)
            segments = [{"start": float(_get(s, "start", 0)), "end": float(_get(s, "end", 0)),
                         "text": _get(s, "text", "")} for s in (_get(resp, "segments") or [])]
            result = {
                "text": segments_to_text(segments) if segments else (_get(resp, "text") or "").strip(),
                "segments": segments,
                "language": _get(resp, "language"),
                "duration_s": _get(resp, "duration") or (segments[-1]["end"] if segments else None),
                "engine": self.engine,
            }
            out.update(chars=len(result["text"]), duration_s=result["duration_s"], language=result["language"])
        return result


class LocalWhisper:
    engine = "local-whisper"

    def __init__(self, model_size: str, inspector: Inspector) -> None:
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except ImportError as exc:
            raise TranscriptionError("TRANSCRIBER=local needs `pip install faster-whisper`") from exc
        self.model_size = model_size
        self.inspector = inspector
        self._model = None

    def _run(self, audio: bytes, language: str | None, prompt: str | None) -> dict[str, Any]:
        from faster_whisper import WhisperModel

        if self._model is None:  # first call downloads the model (~140 MB for "base")
            self._model = WhisperModel(self.model_size, device="auto", compute_type="int8")
        seg_iter, info = self._model.transcribe(io.BytesIO(audio), language=language, initial_prompt=prompt,
                                                vad_filter=True)
        segments = [{"start": s.start, "end": s.end, "text": s.text} for s in seg_iter]
        return {"text": segments_to_text(segments), "segments": segments, "language": info.language,
                "duration_s": info.duration, "engine": self.engine}

    async def transcribe(self, audio: bytes, filename: str, *, language: str | None = None,
                         prompt: str | None = None) -> dict[str, Any]:
        async with self.inspector.track("transcribe", engine=self.engine, model=self.model_size, filename=filename,
                                        bytes=len(audio)) as out:
            result = await asyncio.to_thread(self._run, audio, language, prompt)
            out.update(chars=len(result["text"]), duration_s=result["duration_s"], language=result["language"])
        return result
