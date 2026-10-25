import io
import wave
import queue
import time
import numpy as np
import sounddevice as sd
from config import SAMPLE_RATE, CHANNELS, AUDIO_DTYPE, BLOCK_SIZE
from core.guardian import SerialWorker


class AudioRecorder:
    """Captura del micrófono. Abrir y cerrar el stream ocurre SIEMPRE en su
    propio hilo (`self.worker`), nunca en el hilo principal.

    Por qué (26-sep): cerrar el stream se quedó colgado para siempre dentro de
    `AudioOutputUnitStop` — un interbloqueo entre CoreAudio y PortAudio — y
    como corría en el hilo principal se congelaron orbe, teclas y menú. Ahora
    `start()` y `stop()` regresan al instante; si el hilo de audio se atasca,
    el Guardián lo ve (`worker.atascado()`) y reinicia la app."""

    def __init__(self):
        self.audio_queue = queue.Queue()  # For UI visualization
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.is_recording = False
        self._start_time = 0.0
        self._sample_rate = SAMPLE_RATE  # actual rate of the current recording
        # Cada grabación tiene un número. Un stream viejo que todavía no se
        # cerró no puede meter su audio en la grabación nueva.
        self._gen = 0
        # Micrófono: el que elige la persona en el menú, guardado POR NOMBRE (los
        # índices cambian al conectar cosas). "" = el que la Mac tenga por defecto. Si
        # el elegido no está conectado, se usa el de la Mac: nunca te quedas sin dictado.
        self.mic_preferido = ""
        self.dispositivo_actual = ""        # el que de verdad abrió la última grabación
        self.entradas: list[str] = []       # micrófonos conocidos (se actualizan en el hilo de audio)
        self.worker = SerialWorker("audio")
        self.worker.submit(self._actualizar_entradas, "listar micrófonos")

    def _make_callback(self, gen: int):
        def _callback(indata: np.ndarray, frames: int, time_info, status):
            if gen != self._gen or not self.is_recording:
                return
            if status:
                print(f"Audio status: {status}")
            self.audio_queue.put(indata.copy())
            self.frames.append(indata.copy())
        return _callback

    # -- device / stream helpers ------------------------------------------

    def refresh_devices(self):
        """Re-detecta dispositivos (en el hilo de audio, detrás de lo pendiente)."""
        self.worker.submit(self._refresh_devices_now, "re-detectar dispositivos")

    def _refresh_devices_now(self):
        """Re-initialize PortAudio so it re-enumerates devices.

        PortAudio caches the device list at import time. When the input
        device changes mid-session (e.g. AirPods handing off between Mac,
        iPhone and iPad) that cache goes stale and opening a stream fails.
        Tearing PortAudio down and back up refreshes it.
        """
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:
            print(f"PortAudio refresh failed: {e}")
        self._actualizar_entradas()

    def _actualizar_entradas(self):
        """Nombres de los micrófonos (hilo de audio). El menú los lee de aquí."""
        try:
            nombres = [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
        except Exception as e:
            print(f"query_devices failed: {e}")
            return
        self.entradas = list(dict.fromkeys(nombres))

    def _safe_close_stream(self):
        """Close the stream defensively — never raises, always clears it.

        This is what keeps a failed/interrupted device from staying captured
        (which previously forced a full reboot to release the microphone)."""
        if self.stream is not None:
            try:
                self.stream.stop()
            except Exception as e:
                print(f"stream.stop() failed: {e}")
            try:
                self.stream.close()
            except Exception as e:
                print(f"stream.close() failed: {e}")
            self.stream = None

    def _candidate_configs(self):
        """Ordered (device, samplerate) attempts, most-preferred first.

        Preferred = current default at 16 kHz (smallest upload). Fallbacks add
        the device's native rate and the built-in MacBook mic, so a flaky
        AirPods/iPhone input can't take the whole app down."""
        configs = []
        try:
            devices = sd.query_devices()
        except Exception as e:
            print(f"query_devices failed: {e}")
            return [(None, SAMPLE_RATE), (None, 48000)]

        def add(idx):
            try:
                native = int(round(devices[idx]["default_samplerate"]))
            except Exception:
                native = 48000
            configs.append((idx, SAMPLE_RATE))
            configs.append((idx, native))

        # 0) el micrófono elegido en el menú, si está conectado
        if self.mic_preferido:
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0 and d["name"] == self.mic_preferido:
                    add(i)
                    break

        # 1) system default input
        try:
            default_in = sd.default.device[0]
        except Exception:
            default_in = None
        if isinstance(default_in, int) and default_in >= 0:
            add(default_in)

        # 2) built-in MacBook microphone as a reliable fallback
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and (
                "macbook" in d["name"].lower() or "built-in" in d["name"].lower()
            ):
                add(i)
                break

        # 3) last resort: let PortAudio pick (device=None)
        configs.append((None, SAMPLE_RATE))
        configs.append((None, 48000))

        # de-dup, keep order
        seen, uniq = set(), []
        for c in configs:
            if c not in seen:
                seen.add(c)
                uniq.append(c)
        return uniq

    def _open_stream(self, gen: int) -> sd.InputStream:
        """Try each candidate config until one opens. Raises if all fail."""
        last_err = None
        for device, sr in self._candidate_configs():
            try:
                stream = sd.InputStream(
                    device=device,
                    samplerate=sr,
                    channels=CHANNELS,
                    dtype=AUDIO_DTYPE,
                    blocksize=BLOCK_SIZE,
                    callback=self._make_callback(gen),
                )
                stream.start()
                self._sample_rate = int(sr)
                try:
                    self.dispositivo_actual = sd.query_devices(device if device is not None else
                                                               sd.default.device[0])["name"]
                except Exception:
                    self.dispositivo_actual = ""
                return stream
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"No se pudo abrir ningún micrófono: {last_err}")

    # -- public API -------------------------------------------------------

    def start(self, on_error=None):
        """Empieza a grabar. Regresa al instante: el micrófono se abre en el
        hilo de audio. Si no se puede abrir, llama `on_error(mensaje)` desde
        ese hilo (pásale el `.emit` de una señal Qt, nunca toques la UI)."""
        self._gen += 1
        gen = self._gen
        self.frames = []
        # Drain any old data from the queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        self.is_recording = True
        self._start_time = time.time()
        self.worker.submit(lambda: self._open_now(gen, on_error), "abrir micrófono")

    def _open_now(self, gen: int, on_error):
        # Lo que haya quedado de una sesión anterior (posiblemente fallida)
        self._safe_close_stream()
        if gen != self._gen or not self.is_recording:
            return  # ya soltaron la tecla (toque corto): no abras nada
        try:
            self.stream = self._open_stream(gen)
        except Exception:
            # Device may have changed (AirPods/iPhone handoff): re-enumerate
            # devices once and retry before giving up.
            self._refresh_devices_now()
            try:
                self.stream = self._open_stream(gen)
            except Exception as e:
                if gen == self._gen:
                    self.is_recording = False
                if on_error is not None:
                    on_error(str(e))
                return
        if gen != self._gen or not self.is_recording:
            self._safe_close_stream()   # soltaron la tecla mientras abría

    def stop(self) -> float:
        """Deja de grabar y devuelve la duración. Regresa al instante: el
        stream se cierra en el hilo de audio. `self.frames` ya tiene todo."""
        self.is_recording = False
        duration = time.time() - self._start_time
        self.worker.submit(self._safe_close_stream, "cerrar micrófono")
        return duration

    def get_wav_buffer(self) -> io.BytesIO:
        """Convert recorded frames to in-memory WAV buffer."""
        if not self.frames:
            return io.BytesIO()
        audio_data = np.concatenate(self.frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(self._sample_rate)  # actual capture rate
            wf.writeframes(audio_data.tobytes())
        buf.seek(0)
        return buf

    def nivel_rms(self) -> float:
        """Volumen promedio de la grabación (0..1). Casi 0 = se grabó silencio."""
        if not self.frames:
            return 0.0
        audio = np.concatenate(self.frames, axis=0).astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(audio * audio)))

    def get_duration(self) -> float:
        if not self.frames:
            return 0.0
        total_samples = sum(f.shape[0] for f in self.frames)
        return total_samples / self._sample_rate
