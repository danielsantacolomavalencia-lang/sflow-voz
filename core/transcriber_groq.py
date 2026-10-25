"""Transcripción en la nube: Groq · Whisper Large v3 Turbo.

Es el fallback permanente del router (core/transcriber.py): mientras el modelo
local carga, si no está instalado, o si falla en un dictado.
"""
import io
import os
from groq import Groq
from config import GROQ_MODEL, WHISPER_LANGUAGE


class GroqTranscriber:
    def __init__(self):
        self._client = None

    def _get_client(self) -> Groq:
        """Lazy init: creates client on first use so API key from first-run dialog works."""
        if self._client is None:
            key = os.getenv("GROQ_API_KEY", "")
            if not key:
                raise ValueError("GROQ_API_KEY not configured")
            self._client = Groq(api_key=key, timeout=10.0)
        return self._client

    def transcribe(self, wav_buffer: io.BytesIO, prompt: str = "") -> str:
        """Send WAV audio to Groq Whisper and return the raw text."""
        wav_buffer.seek(0)
        data = wav_buffer.read()
        if len(data) < 100:
            return ""
        kwargs = dict(
            file=("recording.wav", data),
            model=GROQ_MODEL,
            language=WHISPER_LANGUAGE,
            response_format="text",
            temperature=0.0,
        )
        if prompt:
            kwargs["prompt"] = prompt
        transcription = self._get_client().audio.transcriptions.create(**kwargs)
        return transcription.strip() if isinstance(transcription, str) else str(transcription).strip()

    @property
    def model_id(self) -> str:
        return GROQ_MODEL
