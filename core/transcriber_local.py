"""Transcripción LOCAL con mlx-whisper (Whisper corriendo en Apple Silicon vía MLX).

Modelo: mlx-community/whisper-large-v3-turbo — el mismo que usamos en Groq.
Benchmark de Carreón en M4 (voz real es): ~950 ms warm por 10 s de audio,
WER 2.9 % (igual que Groq), offline, $0. mlx-whisper cachea el modelo cargado
por repo, así que tras el `warm()` inicial cada dictado va caliente.

Diferencia con el port de Carreón: él le pasa a mlx-whisper un archivo WAV y
mlx-whisper lo decodifica con `ffmpeg` (dependencia externa). Aquí decodificamos
el WAV con `wave` + numpy y le pasamos el array float32 a 16 kHz. Sin ffmpeg.

Primera vez: descarga ~1.6 GB de Hugging Face a ~/.cache/huggingface. Mientras
tanto `ready` es False y el router manda los dictados a Groq.
"""
import io
import threading
import time
import wave
import numpy as np
from config import LOCAL_MODEL_ID, WHISPER_LANGUAGE, LOCAL_MLX_CACHE_MB

_WHISPER_RATE = 16000


def _mlx_memoria():
    """(set_cache_limit, clear_cache) de MLX, sea cual sea su versión."""
    import mlx.core as mx
    fijar = getattr(mx, "set_cache_limit", None) or mx.metal.set_cache_limit
    limpiar = getattr(mx, "clear_cache", None) or mx.metal.clear_cache
    return fijar, limpiar


def wav_to_float32_16k(wav_buffer: io.BytesIO) -> np.ndarray:
    """WAV int16 (mono o no, cualquier tasa) → float32 mono a 16 kHz, en [-1, 1]."""
    wav_buffer.seek(0)
    with wave.open(wav_buffer, "rb") as wf:
        channels, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"WAV de {width * 8} bits; se esperaba int16")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != _WHISPER_RATE:
        try:
            from scipy.signal import resample_poly   # viene con mlx-whisper
            from math import gcd
            g = gcd(_WHISPER_RATE, rate)
            audio = resample_poly(audio, _WHISPER_RATE // g, rate // g).astype(np.float32)
        except ImportError:   # remuestreo lineal: suficiente para voz
            n = int(len(audio) * _WHISPER_RATE / rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return np.ascontiguousarray(audio, dtype=np.float32)


class LocalTranscriber:
    """mlx-whisper backend. Import perezoso; `warm()` carga el modelo y lo deja residente."""

    def __init__(self, model_id: str = LOCAL_MODEL_ID):
        self._model_id = model_id
        self._import_error: str | None = None
        self._tried_import = False
        self._ready = False
        self._loading = False
        self._error: str | None = None
        self._lock = threading.Lock()   # una sola inferencia a la vez (y no durante la carga)

    # -- estado --------------------------------------------------------------

    @property
    def available(self) -> bool:
        """¿Está instalado mlx-whisper? (En Intel o sin el paquete: no.)"""
        if not self._tried_import:
            self._tried_import = True
            try:
                import mlx_whisper  # noqa: F401
            except Exception as e:
                self._import_error = str(e)
        return self._import_error is None

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def status(self) -> str:
        """Para el menú de la barra."""
        if not self.available:
            return "no disponible (mlx-whisper no instalado)"
        if self._ready:
            return "listo"
        if self._loading:
            return "cargando el modelo…"
        if self._error:
            return f"error: {self._error[:60]}"
        return "sin cargar"

    # -- carga ---------------------------------------------------------------

    def warm(self) -> bool:
        """Carga el modelo (y lo descarga la primera vez) con 0.1 s de silencio.

        Llamar desde un hilo: puede tardar segundos (o minutos la primera vez).
        Devuelve True si quedó listo."""
        if self._ready:
            return True
        if not self.available:
            return False
        with self._lock:
            if self._ready:
                return True
            self._loading, self._error = True, None
            t0 = time.time()
            try:
                import mlx_whisper
                fijar, _ = _mlx_memoria()
                fijar(LOCAL_MLX_CACHE_MB * 1024 * 1024)
                mlx_whisper.transcribe(
                    np.zeros(1600, dtype=np.float32),
                    path_or_hf_repo=self._model_id,
                    language=WHISPER_LANGUAGE,
                    temperature=0.0,
                )
                self._ready = True
                print(f"[SFlow] Whisper local listo ({self._model_id}) en {time.time() - t0:.1f}s")
            except Exception as e:
                self._error = str(e)
                print(f"[SFlow] Whisper local no cargó: {e}")
            finally:
                self._loading = False
        return self._ready

    # -- inferencia ----------------------------------------------------------

    def transcribe(self, wav_buffer: io.BytesIO, prompt: str = "") -> str:
        if not self.available:
            raise RuntimeError(f"mlx-whisper no disponible: {self._import_error}")
        import mlx_whisper
        audio = wav_to_float32_16k(wav_buffer)
        if len(audio) < _WHISPER_RATE // 10:   # < 0.1 s
            return ""
        # Silencio: Whisper alucina sobre él ("Gracias.", créditos de video).
        # Voz normal por este mic ronda 0.03-0.15 RMS; 0.002 es ruido de fondo.
        if float(np.sqrt(np.mean(audio * audio))) < 0.002:
            return ""
        kwargs = dict(path_or_hf_repo=self._model_id, language=WHISPER_LANGUAGE, temperature=0.0)
        if prompt:
            kwargs["initial_prompt"] = prompt   # la pista de nombres propios sobrevive
        with self._lock:
            try:
                result = mlx_whisper.transcribe(audio, **kwargs)
            finally:
                try:
                    _mlx_memoria()[1]()   # suelta la caché de este dictado
                except Exception:
                    pass
        self._ready = True
        return (result.get("text") or "").strip()

    @property
    def model_id(self) -> str:
        return self._model_id
