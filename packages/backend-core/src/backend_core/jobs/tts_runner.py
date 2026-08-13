"""Voiceover synthesis (§30, §31, PHASE 12).

Synthesises the approved script's narration shot by shot, measures each
segment, and stores the timings — which §31's subtitles and §33's timeline both
build on.

Per shot rather than per script, because the timeline needs to know when each
line starts. One long audio file would give the right total and no way to
place a subtitle.
"""

from __future__ import annotations

import io
import uuid
import wave
from dataclasses import dataclass

from backend_core.config import Settings, get_settings
from backend_core.db import get_async_sessionmaker
from backend_core.domain.enums import AssetSourceType, AssetType, JobStatus, UploadStatus
from backend_core.domain.models import MediaAsset, SubtitleTrack, VoiceoverTrack
from backend_core.observability import get_logger
from backend_core.providers.tts import SpeechRequest, get_tts_provider
from backend_core.render.subtitles import build_cues, to_srt
from backend_core.repositories.storyboards import StoryboardRepository
from backend_core.services.jobs import JobService
from backend_core.storage.keys import project_audio_key, project_subtitle_key
from backend_core.storage.s3 import get_storage

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TTSOutcome:
    job_id: uuid.UUID
    status: JobStatus
    voiceover_id: uuid.UUID | None = None
    total_duration_ms: int = 0


class TTSJobRunner:
    """Turns a storyboard's per-shot narration into timed audio (§30)."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._provider = get_tts_provider(self._settings)

    async def run(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> TTSOutcome:
        async with get_async_sessionmaker()() as session:
            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            project_id = job.project_id
            storyboard_id = job.input_json.get("storyboard_id")
            language = str(job.input_json.get("language", "zh-CN"))
            await jobs.transition(job, JobStatus.QUEUED)
            await jobs.transition(job, JobStatus.PROCESSING)
            await session.commit()

        if project_id is None or storyboard_id is None:
            raise ValueError("A TTS job needs a project and a storyboard.")

        async with get_async_sessionmaker()() as session:
            shots = await StoryboardRepository(session).list_shots(
                workspace_id, uuid.UUID(str(storyboard_id))
            )
            lines = [
                (shot.id, shot.voiceover_text.strip(), shot.duration_seconds)
                for shot in shots
                if shot.voiceover_text.strip()
            ]

        if not lines:
            # A purely visual video is legitimate. Completing with zero
            # duration is honest; failing would block a valid project.
            async with get_async_sessionmaker()() as session:
                jobs = JobService(session, settings=self._settings)
                job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
                await jobs.complete(job, output_json={"segments": 0})
                await session.commit()
            return TTSOutcome(job_id=job_id, status=JobStatus.COMPLETED)

        # Typed narrowly rather than `dict[str, object]`: the subtitle
        # builder needs the ints back, and casting at the use site would be
        # hiding the looseness rather than removing it.
        segments: list[dict[str, str | int]] = []
        chunks: list[bytes] = []
        cursor_ms = 0

        for shot_id, text, _slot_seconds in lines:
            result = self._provider.synthesize(SpeechRequest(text=text, language=language))
            chunks.append(result.audio)
            segments.append(
                {
                    "shot_id": str(shot_id),
                    "text": text,
                    "start_ms": cursor_ms,
                    "end_ms": cursor_ms + result.duration_ms,
                }
            )
            cursor_ms += result.duration_ms

        combined = _concat_wav(chunks)

        async with get_async_sessionmaker()() as session:
            storage = get_storage()
            audio_key = project_audio_key(workspace_id, project_id)
            storage.put_bytes(audio_key, combined, "audio/wav")

            audio_asset = MediaAsset(
                workspace_id=workspace_id,
                asset_type=AssetType.AUDIO,
                source_type=AssetSourceType.AI_GENERATED,
                bucket=self._settings.s3_bucket,
                object_key=audio_key,
                original_filename="voiceover.wav",
                mime_type="audio/wav",
                size_bytes=len(combined),
                duration_ms=cursor_ms,
                upload_status=UploadStatus.READY,
                asset_metadata={"provider": self._provider.name, "language": language},
            )
            session.add(audio_asset)
            await session.flush()

            voiceover = VoiceoverTrack(
                workspace_id=workspace_id,
                project_id=project_id,
                language=language,
                voice=f"{language}-default",
                provider=self._provider.name,
                audio_asset_id=audio_asset.id,
                total_duration_ms=cursor_ms,
                segments=segments,
            )
            session.add(voiceover)

            # §31: subtitles are built from these measured timings, in the same
            # transaction, so the two can never describe different narration.
            cues = build_cues(
                [
                    (
                        str(segment["text"]),
                        int(segment["start_ms"]),
                        int(segment["end_ms"]),
                    )
                    for segment in segments
                ]
            )
            srt_key = project_subtitle_key(workspace_id, project_id)
            srt_body = to_srt(cues).encode("utf-8")
            storage.put_bytes(srt_key, srt_body, "application/x-subrip")

            srt_asset = MediaAsset(
                workspace_id=workspace_id,
                asset_type=AssetType.SUBTITLE,
                source_type=AssetSourceType.DERIVED,
                bucket=self._settings.s3_bucket,
                object_key=srt_key,
                original_filename="subtitles.srt",
                mime_type="application/x-subrip",
                size_bytes=len(srt_body),
                upload_status=UploadStatus.READY,
            )
            session.add(srt_asset)
            await session.flush()

            session.add(
                SubtitleTrack(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    language=language,
                    cues=[
                        {"start_ms": cue.start_ms, "end_ms": cue.end_ms, "text": cue.text}
                        for cue in cues
                    ],
                    asset_id=srt_asset.id,
                )
            )

            jobs = JobService(session, settings=self._settings)
            job = await jobs.get(workspace_id=workspace_id, job_id=job_id)
            await jobs.complete(
                job,
                result_asset_id=audio_asset.id,
                output_json={"segments": len(segments), "duration_ms": cursor_ms},
            )
            await session.flush()
            voiceover_id = voiceover.id
            await session.commit()

        logger.info(
            "voiceover_generated",
            extra={"project_id": str(project_id), "segments": len(segments), "ms": cursor_ms},
        )
        return TTSOutcome(
            job_id=job_id,
            status=JobStatus.COMPLETED,
            voiceover_id=voiceover_id,
            total_duration_ms=cursor_ms,
        )


def _concat_wav(chunks: list[bytes]) -> bytes:
    """Join WAV segments into one file.

    Byte concatenation would produce a file with a header in the middle, which
    most players read as the first segment only — a bug that looks like the TTS
    silently truncating. Re-muxing through `wave` is the fix.
    """
    if not chunks:
        return b""

    buffer = io.BytesIO()
    with wave.open(io.BytesIO(chunks[0]), "rb") as first:
        params = first.getparams()

    with wave.open(buffer, "wb") as writer:
        writer.setparams(params)
        for chunk in chunks:
            with wave.open(io.BytesIO(chunk), "rb") as reader:
                writer.writeframes(reader.readframes(reader.getnframes()))

    return buffer.getvalue()


__all__ = ["TTSJobRunner", "TTSOutcome"]
