"""Text-to-speech: LemonFox (primary, natural voice) with macOS `say` fallback.

Patterns learned from a TTS benchmark:
- Prefix "— " per synthesis: LemonFox trims the attack of the FIRST phoneme
  of each request (~90ms → 10ms measured); the dash absorbs the cut so the
  first real word is born whole.
- WAV (not MP3): no encoder priming delay.
- 0.15s of REAL silence prepended to the audio: Bluetooth speakers/AirPods
  wake up late and eat the start — with physical silence up front the first
  word always survives, regardless of device.
- Long texts are split into sentence chunks; the next chunk is fetched WHILE
  the current one plays (prefetch pipeline) so playback starts fast and never
  gaps between chunks.
- LemonFox does NOT expose an account-balance API (verified twice) — usage is
  counted locally via the on_chars callback.
"""

import os
import re
import subprocess
import tempfile
import threading
import time
import wave

import httpx
import numpy as np

from core.lectura import preparar as preparar_lectura

from config import (
    TTS_VOICE,
    TTS_SPEED_DEFAULT,
    LEMONFOX_API_KEY,
    LEMONFOX_TTS_URL,
    LEMONFOX_VOICE,
    LEMONFOX_VOICES,
    LEMONFOX_LANGUAGE,
    LEMONFOX_TIMEOUT,
    LEMONFOX_CHUNK_CHARS,
    APP_DATA_DIR,
)

_SAY_BASE_WPM = 175  # macOS default speaking rate; multiplied by speed
_LEAD_SILENCE_S = 0.15  # physical silence prepended (BT/AirPods shield)


def split_sentences(text: str, max_chars: int = LEMONFOX_CHUNK_CHARS) -> list[str]:
    """Split text into sentence-boundary chunks of at most max_chars.

    Sentences are grouped greedily; a single sentence longer than max_chars
    is hard-split (rare: URLs, code). Never returns empty strings.
    """
    text = (text or "").strip()
    if not text:
        return []
    # Sentence-ish boundaries: ., !, ?, …, newlines — keep the delimiter.
    parts = re.split(r"(?<=[\.\!\?\…\n])\s+", text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        while len(part) > max_chars:  # pathological single "sentence"
            if current:
                chunks.append(current)
                current = ""
            chunks.append(part[:max_chars])
            part = part[max_chars:]
        if not part:
            continue
        if len(current) + len(part) + 1 <= max_chars:
            current = f"{current} {part}".strip()
        else:
            if current:
                chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    return chunks


def _prepend_silence(wav_bytes: bytes, seconds: float = _LEAD_SILENCE_S) -> bytes:
    """Return the WAV with `seconds` of silence up front (same format).

    If anything about the file surprises us, return it untouched — a played
    word beats a perfect buffer.
    """
    import io

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as src:
            params = src.getparams()
            frames = src.readframes(src.getnframes())
        silence = b"\x00" * int(params.framerate * seconds) * params.sampwidth * params.nchannels
        out = io.BytesIO()
        with wave.open(out, "wb") as dst:
            dst.setparams(params)
            dst.writeframes(silence + frames)
        return out.getvalue()
    except Exception:
        return wav_bytes


def _guardar_ultima_lectura(original: str, preparado: str):
    """"Flow, esto se leyó raro": aquí queda el texto original y el que recibió Dora."""
    try:
        with open(os.path.join(APP_DATA_DIR, "ultima_lectura.txt"), "w", encoding="utf-8") as f:
            f.write(f"ORIGINAL:\n{original}\n\nLO QUE RECIBIÓ DORA:\n{preparado}\n")
    except OSError:
        pass


def envolvente(wav_bytes: bytes, fps: int = 30) -> list[float] | None:
    """Volumen de la voz, cuadro por cuadro (0..1), del WAV que va a sonar.

    Sube de inmediato y cae suave (como el micrófono), normalizado al percentil 90
    de lo que sí es voz: así los gestos del orbe caen en las sílabas reales y se
    quedan quietos en las pausas. None si el WAV no se puede leer."""
    import io
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            sr, canales = w.getframerate(), w.getnchannels()
            x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    except Exception:
        return None
    if canales > 1:
        x = x.reshape(-1, canales).mean(axis=1)
    paso = max(1, sr // fps)
    n = len(x) // paso
    if n == 0:
        return None
    rms = np.sqrt(np.mean(x[:n * paso].reshape(n, paso) ** 2, axis=1))
    voz = rms[rms > 0.005]
    ref = float(np.percentile(voz, 90)) if len(voz) else 1.0
    out, nivel = [], 0.0
    for v in rms:
        obj = min(1.0, float(v) / ref)
        nivel = obj if obj > nivel else nivel * 0.62 + obj * 0.38
        out.append(nivel)
    return out


class Speaker:
    """Non-blocking, cancelable reader. LemonFox first, `say` if it fails.

    phase: None | 'fetching' | 'playing' — the pill maps this to its states.
    on_chars(n): called after each successful LemonFox synthesis (usage meter).
    on_fallback(msg): called once per utterance when falling back to `say`.
    """

    def __init__(self):
        self.speed: float = TTS_SPEED_DEFAULT
        self.voice: str = LEMONFOX_VOICE   # menú "Voz de lectura" (settings.json: tts_voice)
        self.phase: str | None = None
        self.on_chars = None
        self.on_fallback = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._gen = 0  # generation counter: stop() bumps it, workers self-abort
        self._lock = threading.Lock()
        # Volumen REAL de lo que suena (1-oct): (inicio monotonic, envolvente a 30 fps).
        # El pill lo lee con nivel_voz() para que el orbe hable con la voz de verdad.
        self._env: tuple[float, list[float]] | None = None
        self._fuente_real = True   # False mientras habla el `say` de respaldo (sin WAV que medir)

    # -- public API -------------------------------------------------------

    def is_speaking(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        return self._proc is not None and self._proc.poll() is None

    def speak(self, text: str, voice: str | None = None) -> bool:
        """Start reading text aloud. Cancels anything in progress.

        `voice`: voz solo para esta lectura (p. ej. «noel» para Claude Code); None = la del menú.
        """
        original = (text or "").strip()
        # Formato → frases, diccionario ("Groq" → "grok") y MAYÚSCULAS (core/lectura.py)
        text = preparar_lectura(original)
        if not text:
            return False
        self.stop()
        self._voz_frase = voice if voice in {v for _, v in LEMONFOX_VOICES} else None
        with self._lock:
            self._gen += 1
            gen = self._gen
        self.phase = "fetching"
        self._fuente_real = True
        self._thread = threading.Thread(target=self._worker, args=(text, gen, original), daemon=True)
        self._thread.start()
        return True

    def nivel_voz(self) -> float | None:
        """Volumen real de lo que está sonando (0..1). 0 entre frases; None si no
        hay audio que medir (voz de respaldo `say`): ahí el pill usa su simulador."""
        env = self._env
        if env is None:
            return 0.0 if self._fuente_real else None
        t0, niveles = env
        i = int((time.monotonic() - t0) * 30)
        return niveles[i] if 0 <= i < len(niveles) else 0.0

    def stop(self):
        """Stop fetch + playback. Never raises."""
        with self._lock:
            self._gen += 1
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self._proc = None
        self.phase = None

    # -- engine internals -------------------------------------------------

    def _alive(self, gen: int) -> bool:
        return gen == self._gen

    def _fetch_chunk(self, text: str) -> bytes | None:
        """One LemonFox synthesis. Returns WAV bytes or None on any failure."""
        if not LEMONFOX_API_KEY:
            return None
        try:
            resp = httpx.post(
                LEMONFOX_TTS_URL,
                headers={
                    "Authorization": f"Bearer {LEMONFOX_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    # "— " absorbs LemonFox's first-phoneme trim (benchmark gotcha)
                    "input": f"— {text}",
                    "voice": getattr(self, "_voz_frase", None) or self.voice,
                    "language": LEMONFOX_LANGUAGE,
                    "response_format": "wav",
                    "speed": self.speed,
                },
                timeout=LEMONFOX_TIMEOUT,
            )
            if resp.status_code != 200 or not resp.content:
                print(f"[SFlow] LemonFox HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            if callable(self.on_chars):
                try:
                    self.on_chars(len(text) + 2)  # +2: the "— " prefix also bills
                except Exception:
                    pass
            return resp.content
        except Exception as e:
            print(f"[SFlow] LemonFox falló: {e}")
            return None

    def _play_wav(self, wav_bytes: bytes, gen: int):
        """Blocking playback of one WAV via afplay (cancelable through stop())."""
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(wav_bytes)
                path = f.name
            if not self._alive(gen):
                return
            niveles = envolvente(wav_bytes)
            self._proc = subprocess.Popen(["afplay", path])
            if niveles:
                self._env = (time.monotonic() + 0.08, niveles)   # afplay tarda ~80 ms en sonar
            self._proc.wait()
        except Exception as e:
            print(f"[SFlow] afplay falló: {e}")
        finally:
            self._proc = None
            self._env = None
            if path:
                try:
                    os.unlink(path)
                except Exception:
                    pass

    def _fallback_say(self, text: str, gen: int):
        """Read with macOS `say` (offline). Speed maps to words per minute."""
        if not self._alive(gen):
            return
        self._fuente_real = False   # `say` no da audio que medir: el pill simula el ritmo
        if callable(self.on_fallback):
            try:
                self.on_fallback("LemonFox no disponible — leyendo con voz local (Paulina).")
            except Exception:
                pass
        try:
            self.phase = "playing"
            self._proc = subprocess.Popen(
                ["say", "-v", TTS_VOICE, "-r", str(int(_SAY_BASE_WPM * self.speed)), text]
            )
            self._proc.wait()
        except Exception as e:
            print(f"[SFlow] say falló: {e}")
        finally:
            self._proc = None

    def _worker(self, text: str, gen: int, original: str = ""):
        """Chunked pipeline: fetch chunk i+1 while chunk i plays."""
        _guardar_ultima_lectura(original, text)
        try:
            chunks = split_sentences(text)
            if not chunks:
                return

            first = self._fetch_chunk(chunks[0])
            if not self._alive(gen):
                return
            if first is None:
                # LemonFox down on the very first chunk → whole text via say
                self._fallback_say(text, gen)
                return

            pending = _prepend_silence(first)
            for i in range(len(chunks)):
                if not self._alive(gen):
                    return
                # Prefetch next chunk in parallel with current playback
                next_wav: dict = {}
                prefetch = None
                if i + 1 < len(chunks):
                    def _pf(idx=i + 1):
                        next_wav["data"] = self._fetch_chunk(chunks[idx])
                    prefetch = threading.Thread(target=_pf, daemon=True)
                    prefetch.start()

                self.phase = "playing"
                self._play_wav(pending, gen)

                if prefetch is not None:
                    prefetch.join()
                if i + 1 < len(chunks):
                    data = next_wav.get("data")
                    if data is None:
                        # Mid-text failure: finish the remaining text with say
                        rest = " ".join(chunks[i + 1:])
                        self._fallback_say(rest, gen)
                        return
                    pending = _prepend_silence(data)
        finally:
            if self._alive(gen):
                self.phase = None
