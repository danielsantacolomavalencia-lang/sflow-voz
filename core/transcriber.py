"""Router de transcripción: audio → (Whisper local en MLX | Groq) → texto limpio.

- Motor preferido en `engine` ("local" | "groq"; el default vive en config y el
  usuario lo cambia desde la barra, persistido en settings.json).
- Local solo atiende cuando ya está cargado (`ready`); mientras carga, o si
  falla en un dictado, el mismo dictado cae a Groq. Nunca se espera al modelo.
- Post-proceso común a ambos: guard del eco del prompt, filtro de alucinaciones
  de Whisper y `STT_CORRECTIONS` (nombres propios que Whisper no adivina).
"""
import io
import queue
import re
import threading
import time
import wave
from config import (STT_PROMPT, STT_CORRECTIONS, STT_HALLUCINATIONS, STT_ENGINE_DEFAULT,
                    STT_LOCAL_LIMITE_BASE_S, STT_LOCAL_LIMITE_POR_S, STT_TOPE_TOTAL_S)
from core.transcriber_groq import GroqTranscriber
from core.transcriber_local import LocalTranscriber

# Compiled once: applied to every transcription.
_CORRECTIONS = [(re.compile(pattern), repl) for pattern, repl in STT_CORRECTIONS]


def apply_corrections(text: str) -> str:
    """Fix proper nouns Whisper can't infer (config.STT_CORRECTIONS)."""
    for pattern, repl in _CORRECTIONS:
        text = pattern.sub(repl, text)
    return text


# Palabras sueltas que Whisper suelta sobre silencio (medido: 1 s de ceros → "Gracias.").
# Comparación EXACTA, no por substring: "gracias" dentro de una frase es dictado real.
_HALLUCINATIONS_EXACT = {"gracias", "gracias por ver", "suscríbete", "adiós"}


def is_hallucination(text: str) -> bool:
    """Whisper inventa créditos de video sobre silencio/ruido: eso no es dictado."""
    lowered = text.lower()
    if lowered.strip(" .!¡") in _HALLUCINATIONS_EXACT:
        return True
    return any(marker in lowered for marker in STT_HALLUCINATIONS)


class Transcriber:
    def __init__(self):
        self._groq = GroqTranscriber()
        self._local = LocalTranscriber()
        self.engine = STT_ENGINE_DEFAULT
        self.last_engine_used: str | None = None   # "local" | "groq" | "groq (local lento)"…
        self.last_seconds: float = 0.0             # cuánto tardó la última transcripción

    @property
    def local(self) -> LocalTranscriber:
        return self._local

    def warm_local(self) -> bool:
        """Precarga el modelo local si es el motor elegido. Llamar desde un hilo."""
        if self.engine != "local":
            return False
        return self._local.warm()

    def status_text(self) -> str:
        if self.engine == "groq":
            return "Transcripción: Groq (nube)"
        return f"Transcripción: local — {self._local.status}"

    def _raw(self, data: bytes, audio_s: float) -> tuple[str, str]:
        if self.engine == "local" and self._local.ready:
            return self._carrera(data, audio_s)
        return self._groq.transcribe(io.BytesIO(data), prompt=STT_PROMPT), "groq"

    def _carrera(self, data: bytes, audio_s: float) -> tuple[str, str]:
        """Local primero. Si tarda mucho más de lo normal para este audio, el
        mismo audio va también a Groq y gana el primero que termine.

        El local nunca se cancela (MLX no se puede interrumpir); si pierde, su
        resultado se descarta. Si el local se quedara atascado, su candado hace
        que los siguientes dictados también pierdan la carrera y vayan a Groq:
        el usuario no espera. Y pase lo que pase hay un tope total, para que el
        orbe jamás se quede "pensando" para siempre."""
        resultados: queue.Queue = queue.Queue()

        def correr(motor, fn):
            try:
                resultados.put((motor, fn(io.BytesIO(data), prompt=STT_PROMPT), None))
            except Exception as e:
                resultados.put((motor, None, e))

        threading.Thread(target=correr, args=("local", self._local.transcribe),
                         daemon=True, name="stt-local").start()
        limite = STT_LOCAL_LIMITE_BASE_S + STT_LOCAL_LIMITE_POR_S * audio_s
        try:
            motor, texto, err = resultados.get(timeout=limite)
            if err is None:
                return texto, "local"
            print(f"[SFlow] Whisper local falló, este dictado va a Groq: {err}")
            return self._groq.transcribe(io.BytesIO(data), prompt=STT_PROMPT), "groq (local falló)"
        except queue.Empty:
            print(f"[SFlow] Whisper local pasa de {limite:.1f} s con {audio_s:.1f} s de audio: "
                  f"lanzo Groq en paralelo.")

        threading.Thread(target=correr, args=("groq", self._groq.transcribe),
                         daemon=True, name="stt-groq").start()
        fin = time.monotonic() + STT_TOPE_TOTAL_S + audio_s
        errores = []
        while len(errores) < 2:
            restante = fin - time.monotonic()
            if restante <= 0:
                break
            try:
                motor, texto, err = resultados.get(timeout=restante)
            except queue.Empty:
                break
            if err is None:
                return texto, ("groq (local lento)" if motor == "groq" else "local (lento)")
            errores.append(err)
        if errores:
            raise errores[-1]
        raise TimeoutError("La transcripción tardó demasiado")

    @staticmethod
    def _duracion(data: bytes) -> float:
        try:
            with wave.open(io.BytesIO(data), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except Exception:
            return 0.0

    def transcribe(self, wav_buffer: io.BytesIO) -> str:
        """Devuelve el texto final ("" si no hubo voz)."""
        wav_buffer.seek(0)
        data = wav_buffer.getvalue()
        if len(data) < 100:
            return ""
        t0 = time.monotonic()
        text, used = self._raw(data, self._duracion(data))
        self.last_seconds = time.monotonic() - t0
        self.last_engine_used = used
        text = text.strip()
        # On silence/noise Whisper sometimes echoes the prompt back verbatim.
        if not text or text == STT_PROMPT.strip() or is_hallucination(text):
            return ""
        return apply_corrections(text)
