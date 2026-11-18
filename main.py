#!/usr/bin/env python3
"""SFlow - Voice-to-text desktop tool powered by Groq Whisper."""

import sys
import multiprocessing


def _desviar_ayudante_multiprocessing():
    """Si nos invocaron como ayudante de multiprocessing, NO somos la app.

    Dentro del .app, sys.executable es SFlow. Cuando algo (tqdm, un Lock de
    multiprocessing) arranca el resource_tracker, ejecuta
    `SFlow [-X utf8] -c "from multiprocessing.resource_tracker import main;main(fd)"`.
    Eso debe correr el ayudante y salir — JAMÁS arrancar la app. El 1-sep, sin
    esto, el bundle se clonó sin fin y dejó la Mac sin RAM. Va ANTES de los
    imports pesados para que el ayudante no cargue Qt ni AppKit de balde.
    PyInstaller trae su propio desvío en freeze_support(), pero aquí no se
    confía en él: este es explícito y se prueba en cada build (ver memoria)."""
    argv = sys.argv
    if "-c" in argv[1:]:
        i = argv.index("-c")
        cmd = argv[i + 1] if len(argv) > i + 1 else ""
        if "multiprocessing" in cmd:
            exec(cmd)
        sys.exit(0)   # cualquier otro -c tampoco es un arranque legítimo
    if any(a.startswith("--multiprocessing") for a in argv[1:]):
        multiprocessing.freeze_support()
        sys.exit(0)


if __name__ == "__main__":
    _desviar_ayudante_multiprocessing()

import os
import time
import signal
import subprocess
import threading
import fcntl
import json
import secrets
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu,
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox,
)
from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QPixmap, QAction, QActionGroup

from ui.pill_widget import PillWidget
from ui.orb import PALETAS, PALETA_DEFAULT
from core.recorder import AudioRecorder
from core.transcriber import Transcriber
from core.tts import Speaker
from core.hotkey import HotkeyListener
from core.clipboard import (
    paste_text, capturar_destino, get_selected_or_clipboard_text, trace,
)
from core.guardian import Guardian, SerialWorker, relanzar, instancia_colgada
from core.energia import con_cargador
from core.settings import load_settings, save_settings
from config import (
    PILL_SIZE_PRESETS,
    PILL_SIZE_DEFAULT,
    LEMONFOX_DASHBOARD_URL,
    LEMONFOX_CHARS_PER_CYCLE,
    LEMONFOX_CYCLE_DAY,
    TTS_SPEED_DEFAULT,
    LEMONFOX_VOICE,
    LEMONFOX_VOICES,
)

# Voice speed presets (multiplier passed to LemonFox `speed`; say maps to WPM)
TTS_SPEED_PRESETS = [("1.0× (normal)", 1.0), ("1.2×", 1.2), ("1.5×", 1.5), ("2.0×", 2.0)]

# Seed for the local usage meter: chars already consumed in the CURRENT cycle
# when the meter was born (read from the LemonFox dashboard on 2026-07-19).
LEMONFOX_SEED_CHARS = 3_974
from db.database import TranscriptionDB
from web.server import start_web_server, registrar_voz
from config import LOGO_PATH, APP_DATA_DIR, GROQ_API_KEY, STT_ENGINES, STT_ENGINE_DEFAULT


def _ensure_accessibility() -> bool:
    """Prompt macOS to grant Accessibility if not trusted. Returns True if already trusted."""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        return AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
    except Exception:
        return True


# LaunchAgent constants
_LAUNCH_AGENT_LABEL = "so.saasfactory.sflow"
_PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{_LAUNCH_AGENT_LABEL}.plist")


# ---------------------------------------------------------------------------
# First-run dialog
# ---------------------------------------------------------------------------
class FirstRunDialog(QDialog):
    """Shown when GROQ_API_KEY is missing on first launch."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SFlow - Setup")
        self.setFixedWidth(420)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Ingresa tu Groq API Key para transcripciones:"))

        link = QLabel('<a href="https://console.groq.com/keys">Obtener gratis en console.groq.com/keys</a>')
        link.setOpenExternalLinks(True)
        layout.addWidget(link)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("gsk_...")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.key_input)

        save_btn = QPushButton("Guardar y continuar")
        save_btn.clicked.connect(self._save_key)
        layout.addWidget(save_btn)

        self.setLayout(layout)

    def _save_key(self):
        key = self.key_input.text().strip()
        if not key.startswith("gsk_") or len(key) < 20:
            QMessageBox.warning(self, "Error", "La clave debe comenzar con 'gsk_' y tener al menos 20 caracteres.")
            return

        env_path = os.path.join(APP_DATA_DIR, ".env")
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        with open(env_path, "w") as f:
            f.write(f"GROQ_API_KEY={key}\n")

        # Set in current process so Transcriber picks it up
        os.environ["GROQ_API_KEY"] = key
        self.accept()


# ---------------------------------------------------------------------------
# Launch at Login
# ---------------------------------------------------------------------------
def _is_launch_at_login() -> bool:
    return os.path.exists(_PLIST_PATH)


def _launch_args() -> list[str]:
    """Cómo relanzar SFlow al iniciar sesión.

    En el .app: `open -a SFlow.app` — así macOS lo trata como
    app (icono, permisos, un solo proceso). En dev: el python del venv + main.py."""
    if getattr(sys, "frozen", False):
        bundle = os.path.abspath(os.path.join(sys.executable, "..", "..", ".."))
        if not bundle.startswith("/Applications/"):
            # Un bundle de dist/ o de la Papelera JAMÁS va al arranque de sesión:
            # el 1-sep un LaunchAgent apuntó a dist/ y relanzaba la bomba en cada
            # inicio hasta que hubo que entrar en modo seguro.
            return []
        return ["/usr/bin/open", "-a", bundle]
    return [sys.executable, os.path.abspath(sys.argv[0])]


def _set_launch_at_login(enabled: bool):
    if enabled and not _launch_args():
        return   # no hay un destino seguro al que apuntar
    if enabled:
        args = "\n".join(f"        <string>{a}</string>" for a in _launch_args())
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCH_AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>"""
        os.makedirs(os.path.dirname(_PLIST_PATH), exist_ok=True)
        with open(_PLIST_PATH, "w") as f:
            f.write(plist)
        subprocess.run(["launchctl", "load", _PLIST_PATH], capture_output=True)
    else:
        if os.path.exists(_PLIST_PATH):
            subprocess.run(["launchctl", "unload", _PLIST_PATH], capture_output=True)
            os.remove(_PLIST_PATH)


def _refresh_launch_agent():
    """Si el LaunchAgent existe pero apunta a otro sitio (p. ej. al main.py de
    dev cuando ya corre el .app), lo reescribe. Se llama en cada arranque."""
    if not _is_launch_at_login():
        return
    try:
        actual = open(_PLIST_PATH).read()
    except OSError:
        return
    args = _launch_args()
    if not args or all(a in actual for a in args):
        return
    _set_launch_at_login(True)


# ---------------------------------------------------------------------------
# System tray
# ---------------------------------------------------------------------------
def _setup_tray(app: QApplication, port: int, sflow=None) -> QSystemTrayIcon:
    pixmap = QPixmap(LOGO_PATH)
    if pixmap.isNull():
        # Fallback: empty icon (shouldn't happen but don't crash)
        icon = QIcon()
    else:
        # Fill the menu bar height and stay crisp on retina; macOS fits it to the bar.
        icon = QIcon(pixmap.scaledToHeight(44, Qt.TransformationMode.SmoothTransformation))

    tray = QSystemTrayIcon(icon, app)

    menu = QMenu()

    status = QAction("SFlow - Activo", menu)
    status.setEnabled(False)
    menu.addAction(status)
    menu.addSeparator()

    dashboard = QAction(f"Abrir Dashboard (:{port})", menu)
    dashboard.triggered.connect(lambda: subprocess.run(["open", f"http://localhost:{port}"], capture_output=True))
    menu.addAction(dashboard)
    menu.addSeparator()

    if sflow is not None:
        reset_audio_action = QAction("Reiniciar audio", menu)
        reset_audio_action.triggered.connect(lambda: sflow.reset_audio())
        menu.addAction(reset_audio_action)

        # Micrófono (18-oct): fijar el de SFlow sin depender del que elija la Mac
        mic_menu = menu.addMenu("Micrófono")

        def _armar_mic_menu():
            mic_menu.clear()
            usando = sflow.recorder.dispositivo_actual or "—"
            info = QAction(f"Usando: {usando}", mic_menu)
            info.setEnabled(False)
            mic_menu.addAction(info)
            mic_menu.addSeparator()
            grupo = QActionGroup(mic_menu)
            grupo.setExclusive(True)
            elegido = sflow.recorder.mic_preferido
            opciones = [("Por defecto de la Mac", "")] + [(n, n) for n in sflow.recorder.entradas]
            if elegido and elegido not in sflow.recorder.entradas:
                opciones.append((f"{elegido} (no conectado)", elegido))
            for etiqueta, nombre in opciones:
                act = QAction(etiqueta, mic_menu)
                act.setCheckable(True)
                act.setChecked(nombre == elegido)
                act.triggered.connect(lambda checked, n=nombre: sflow.set_mic(n))
                grupo.addAction(act)
                mic_menu.addAction(act)
            mic_menu.addSeparator()
            buscar = QAction("Buscar micrófonos de nuevo", mic_menu)
            buscar.triggered.connect(lambda: sflow.reset_audio())
            mic_menu.addAction(buscar)

        mic_menu.aboutToShow.connect(_armar_mic_menu)
        _armar_mic_menu()

        restart_action = QAction("Reiniciar SFlow", menu)
        restart_action.triggered.connect(lambda: sflow.reiniciar())
        menu.addAction(restart_action)

        size_menu = menu.addMenu("Tamaño del orbe")
        size_group = QActionGroup(size_menu)
        size_group.setExclusive(True)
        for label, px in PILL_SIZE_PRESETS:
            act = QAction(label, size_menu)
            act.setCheckable(True)
            act.setChecked(px == sflow.pill._size)
            act.triggered.connect(lambda checked, v=px: sflow.set_pill_size(v))
            size_group.addAction(act)
            size_menu.addAction(act)

        color_menu = menu.addMenu("Color del orbe")
        color_group = QActionGroup(color_menu)
        color_group.setExclusive(True)
        for clave, (nombre, _, _) in PALETAS.items():
            act = QAction(nombre, color_menu)
            act.setCheckable(True)
            act.setChecked(clave == sflow.pill._paleta)
            act.triggered.connect(lambda checked, k=clave: sflow.set_orb_paleta(k))
            color_group.addAction(act)
            color_menu.addAction(act)

        reposo = QAction("Movimiento en reposo (con cargador)", menu)
        reposo.setCheckable(True)
        reposo.setChecked(sflow.pill.reposo_vivo)
        reposo.toggled.connect(lambda on: sflow.set_reposo_vivo(on))
        menu.addAction(reposo)

        stt_menu = menu.addMenu("Transcripción")
        stt_status = QAction(sflow.transcriber.status_text(), stt_menu)
        stt_status.setEnabled(False)
        stt_menu.addAction(stt_status)
        stt_menu.addSeparator()
        stt_group = QActionGroup(stt_menu)
        stt_group.setExclusive(True)
        for label, engine in STT_ENGINES:
            act = QAction(label, stt_menu)
            act.setCheckable(True)
            act.setChecked(engine == sflow.transcriber.engine)
            act.triggered.connect(lambda checked, e=engine: sflow.set_stt_engine(e))
            stt_group.addAction(act)
            stt_menu.addAction(act)
        menu.aboutToShow.connect(lambda: stt_status.setText(sflow.transcriber.status_text()))

        speed_menu = menu.addMenu("Velocidad de voz")
        speed_group = QActionGroup(speed_menu)
        speed_group.setExclusive(True)
        for label, speed in TTS_SPEED_PRESETS:
            act = QAction(label, speed_menu)
            act.setCheckable(True)
            act.setChecked(abs(speed - sflow.speaker.speed) < 0.01)
            act.triggered.connect(lambda checked, sp=speed: sflow.set_tts_speed(sp))
            speed_group.addAction(act)
            speed_menu.addAction(act)

        voice_menu = menu.addMenu("Voz de lectura")
        voice_group = QActionGroup(voice_menu)
        voice_group.setExclusive(True)
        for label, voice in LEMONFOX_VOICES:
            act = QAction(label, voice_menu)
            act.setCheckable(True)
            act.setChecked(voice == sflow.speaker.voice)
            act.triggered.connect(lambda checked, v=voice, n=label: sflow.set_tts_voice(v, n))
            voice_group.addAction(act)
            voice_menu.addAction(act)

        voz_claude = QAction("Leer en voz los resúmenes de Claude", menu)
        voz_claude.setCheckable(True)
        voz_claude.setChecked(sflow.voz_claude)
        voz_claude.toggled.connect(lambda on: sflow.set_voz_claude(on))
        menu.addAction(voz_claude)

        menu.addSeparator()

        # LemonFox usage meter (local count — their API has no balance endpoint)
        lf_status = QAction(sflow.lemonfox_status_text(), menu)
        lf_status.setEnabled(False)
        menu.addAction(lf_status)
        menu.aboutToShow.connect(lambda: lf_status.setText(sflow.lemonfox_status_text()))

        lf_dash = QAction("Abrir dashboard LemonFox", menu)
        lf_dash.triggered.connect(
            lambda: subprocess.run(["open", LEMONFOX_DASHBOARD_URL], capture_output=True))
        menu.addAction(lf_dash)

    menu.addSeparator()

    login_action = QAction("Iniciar con macOS", menu)
    login_action.setCheckable(True)
    login_action.setChecked(_is_launch_at_login())
    login_action.toggled.connect(_set_launch_at_login)
    menu.addAction(login_action)
    menu.addSeparator()

    quit_action = QAction("Salir", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.setToolTip("SFlow - Voice to Text")
    tray.show()
    return tray


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------
def _friendly_error(err: str) -> str:
    """Translate a raw transcription error into an actionable Spanish message."""
    s = (err or "").lower()
    if "403" in s or "network settings" in s or "permissiondenied" in s or "access denied" in s:
        return "Groq bloqueó la conexión (403). ¿Tienes un VPN activo? Desconéctalo e intenta de nuevo."
    if "401" in s or "invalid api key" in s or "authentication" in s or "api key" in s:
        return "Tu API key de Groq no es válida. Revísala en los ajustes."
    if "timeout" in s or "timed out" in s:
        return "Groq tardó demasiado en responder. Revisa tu conexión a internet."
    if ("connection" in s or "getaddrinfo" in s or "name or service" in s
            or "failed to establish" in s or "unreachable" in s):
        return "Sin conexión a internet o no se pudo contactar a Groq."
    if "no speech" in s:
        return "No se detectó voz en el audio."
    return f"No se pudo transcribir: {err}"


# ---------------------------------------------------------------------------
# Main app controller
# ---------------------------------------------------------------------------
class SFlowApp(QObject):
    """Main application controller. Wires hotkey -> recorder -> transcriber -> clipboard."""

    transcription_done = pyqtSignal(str, float, object)   # texto, duración, meta (destino, motor, seg)
    transcription_error = pyqtSignal(str)
    # Hilos de trabajo → hilo principal
    mic_error = pyqtSignal(str)          # el micrófono no abrió (hilo de audio)
    pegado = pyqtSignal(bool)            # terminó el pegado (hilo "sistema")
    cargador = pyqtSignal(bool)          # ¿la Mac está conectada? (hilo "sistema", cada 30 s)
    texto_para_leer = pyqtSignal(str)    # selección capturada para leer en voz alta
    voz_de_claude = pyqtSignal(int, str, str)  # (id, frase 🔊, voz de la frase o "") que manda el gancho de Claude Code (hilo de Flask)
    # TTS worker thread → main thread (Qt objects must not be touched from workers)
    lemonfox_chars = pyqtSignal(int)
    tts_fallback = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.recorder = AudioRecorder()
        self.recorder.mic_preferido = str(load_settings().get("mic", ""))
        self._ultimo_aviso_silencio = 0.0
        self.transcriber = Transcriber()
        self.transcriber.engine = load_settings().get("stt_engine", STT_ENGINE_DEFAULT)
        self.speaker = Speaker()
        self.speaker.speed = float(load_settings().get("tts_speed", TTS_SPEED_DEFAULT))
        self.speaker.voice = str(load_settings().get("tts_voice", LEMONFOX_VOICE))
        # Speaker callbacks fire in its worker thread → bounce through signals
        self.speaker.on_chars = self.lemonfox_chars.emit
        self.speaker.on_fallback = self.tts_fallback.emit
        self.db = TranscriptionDB()
        self.hotkey = HotkeyListener()
        self.pill = PillWidget(size_px=load_settings().get("pill_size", PILL_SIZE_DEFAULT),
                               paleta=load_settings().get("orb_paleta", PALETA_DEFAULT))
        self.tray = None  # set by main() once the tray icon exists

        # Mic level drives the orb while it listens; the real voice level while it speaks
        self.pill.set_audio_queue(self.recorder.audio_queue)
        self.pill.set_fuente_voz(self.speaker.nivel_voz)
        self.pill.reposo_vivo = bool(load_settings().get("reposo_vivo", True))

        # MUST use QueuedConnection: pynput emits from its own thread
        self.hotkey.pressed.connect(self._on_hotkey_pressed, Qt.ConnectionType.QueuedConnection)
        self.hotkey.released.connect(self._on_hotkey_released, Qt.ConnectionType.QueuedConnection)
        self.transcription_done.connect(self._on_transcription_done, Qt.ConnectionType.QueuedConnection)
        self.transcription_error.connect(self._on_transcription_error, Qt.ConnectionType.QueuedConnection)
        self.mic_error.connect(self._on_mic_error, Qt.ConnectionType.QueuedConnection)
        self.pegado.connect(self._on_pegado, Qt.ConnectionType.QueuedConnection)
        self.cargador.connect(self._on_cargador, Qt.ConnectionType.QueuedConnection)
        self.texto_para_leer.connect(self._on_texto_para_leer, Qt.ConnectionType.QueuedConnection)
        self.voz_de_claude.connect(self._on_voz_de_claude, Qt.ConnectionType.QueuedConnection)

        # Lo que puede bloquearse fuera de nuestro control (osascript, foco,
        # capturar la selección) corre aquí, no en el hilo principal.
        self.io = SerialWorker("sistema")
        self._destino = None   # a dónde va el dictado en curso

        # Guardián: si el hilo principal o el de audio se atascan, reinicia.
        self.guardian = Guardian(limite_principal=10.0)
        self.guardian.vigilar("audio", self.recorder.worker, 8.0)
        self.guardian.vigilar("sistema", self.io, 20.0)
        self._latido = QTimer()
        self._latido.setInterval(1000)
        self._latido.timeout.connect(self.guardian.latido)

        # Reposo vivo solo con el cargador: con batería el orbe se congela en reposo.
        self._energia = QTimer()
        self._energia.setInterval(30_000)
        self._energia.timeout.connect(self._consultar_cargador)
        self.hotkey.speak_toggle.connect(self._on_speak_toggle, Qt.ConnectionType.QueuedConnection)
        self.lemonfox_chars.connect(self._on_lemonfox_chars, Qt.ConnectionType.QueuedConnection)
        self.tts_fallback.connect(self._on_tts_fallback, Qt.ConnectionType.QueuedConnection)

        # Poll to return the pill to idle once speech finishes on its own
        self._speak_timer = QTimer()
        self._speak_timer.setInterval(250)
        self._speak_timer.timeout.connect(self._check_speaking)

        # Resúmenes en voz de Claude Code: en fila, y solo cuando SFlow está libre
        # (no mientras dictas ni mientras ya está hablando).
        self.voz_claude = bool(load_settings().get("voz_claude", True))
        self._voz_fila: list[tuple[int, str, str]] = []
        self._voz_actual: int | None = None   # id de la frase que suena ahora
        self._voz_terminado = 0               # frases 1..n ya dichas o descartadas (lo lee /voz/estado)
        self._voz_timer = QTimer()
        self._voz_timer.setInterval(400)
        self._voz_timer.timeout.connect(self._hablar_siguiente)

    def start(self):
        self.hotkey.start()
        self.pill.show()
        self.pill.set_state(PillWidget.STATE_IDLE)
        self._warm_local_stt()
        self._latido.start()
        self.guardian.start()
        self._consultar_cargador()
        self._energia.start()
        self._simular_cuelgue_si_se_pide()

    def _simular_cuelgue_si_se_pide(self):
        """Solo para probar el guardián: SFLOW_SIMULAR=audio|principal.
        `open` no pasa variables de entorno, así que la instancia que el
        guardián relanza siempre arranca limpia (no hay bucle posible)."""
        que = os.getenv("SFLOW_SIMULAR", "")
        if que == "audio":
            print("[SFlow] SIMULACIÓN: el hilo de audio se atasca en 3 s")
            QTimer.singleShot(3000, lambda: self.recorder.worker.submit(
                lambda: time.sleep(10**6), "cerrar micrófono (simulado)"))
        elif que == "principal":
            print("[SFlow] SIMULACIÓN: el hilo principal se congela en 3 s")
            QTimer.singleShot(3000, lambda: time.sleep(10**6))

    def reiniciar(self):
        """Menú 'Reiniciar SFlow': cierra y vuelve a abrir (sin freno: es a propósito)."""
        relanzar("pedido desde el menú", automatico=False)

    def _warm_local_stt(self):
        """Carga el Whisper local en un hilo. Mientras tanto los dictados van a Groq."""
        if self.transcriber.engine != "local" or self.transcriber.local.ready:
            return
        threading.Thread(target=self.transcriber.warm_local, daemon=True, name="stt-warm").start()

    def set_stt_engine(self, engine: str):
        """Local (M4) o Groq (nube), desde la barra; se recuerda entre reinicios."""
        self.transcriber.engine = engine
        s = load_settings()
        s["stt_engine"] = engine
        save_settings(s)
        self._warm_local_stt()

    @pyqtSlot()
    def _on_hotkey_pressed(self):
        # Todo aquí regresa al instante: el micrófono se abre en el hilo de
        # audio. Si no abre (p. ej. AirPods cambiando), llega mic_error.
        if self.speaker.is_speaking():
            # Vas a hablar tú: SFlow se calla (si no, el micro graba la voz de Dora).
            self.speaker.stop()
            self._speak_timer.stop()
        self._voz_descartar()
        self._destino = capturar_destino()
        self.recorder.start(on_error=self.mic_error.emit)
        self.pill.set_state(PillWidget.STATE_RECORDING)
        trace("pill=grabando")

    @pyqtSlot()
    def _on_hotkey_released(self):
        trace("Ctrl: parar")
        try:
            duration = self.recorder.stop()
            trace("mic cerrado")
        except Exception as e:
            print(f"Error al detener grabación: {e}")
            self.pill.set_state(PillWidget.STATE_IDLE)
            return

        # Nothing captured (start likely failed) → back to idle, no processing
        if not self.recorder.frames:
            self.pill.set_state(PillWidget.STATE_IDLE)
            return

        self.pill.set_state(PillWidget.STATE_PROCESSING)
        trace("pill=procesando")

        if duration < 0.3:
            self.pill.set_state(PillWidget.STATE_IDLE)
            return

        # Silencio (18-oct): el 18-oct la Mac cambió sola el micrófono a la cámara web y
        # cada dictado salía "No speech detected" sin pista de por qué. Ahora se dice.
        if duration >= 1.0 and self.recorder.nivel_rms() < 0.002:
            self._avisar_silencio()
            return

        wav_buffer = self.recorder.get_wav_buffer()
        recording_duration = self.recorder.get_duration()
        destino, self._destino = self._destino, None
        thread = threading.Thread(
            target=self._transcribe_worker,
            args=(wav_buffer, recording_duration, destino, self.recorder.nivel_rms()),
            daemon=True,
        )
        thread.start()

    @pyqtSlot(str)
    def _on_mic_error(self, err: str):
        print(f"[SFlow] Error al iniciar grabación: {err}")
        self.pill.set_state(PillWidget.STATE_ERROR)

    def _avisar_silencio(self):
        mic = self.recorder.dispositivo_actual or "el micrófono de la Mac"
        print(f"[SFlow] dictado en silencio · micrófono «{mic}» · rms {self.recorder.nivel_rms():.4f}")
        self.pill.set_state(PillWidget.STATE_ERROR)
        if self.tray is not None and time.time() - self._ultimo_aviso_silencio > 30:
            self._ultimo_aviso_silencio = time.time()
            self.tray.showMessage(
                "SFlow", f"Grabé silencio con «{mic}». Revisa el menú Micrófono de SFlow.",
                QSystemTrayIcon.MessageIcon.Warning, 6000)

    def set_mic(self, nombre: str):
        """Menú "Micrófono": fija el de SFlow ("" = el de la Mac). Vale desde el próximo dictado."""
        self.recorder.mic_preferido = nombre
        s = load_settings()
        s["mic"] = nombre
        save_settings(s)
        print(f"[SFlow] micrófono de SFlow: {nombre or 'el de la Mac por defecto'}")

    def reset_audio(self):
        """Manually re-detect audio devices (menu bar 'Reiniciar audio')."""
        if self.recorder.is_recording:
            return
        self.recorder.refresh_devices()   # corre en el hilo de audio
        print("[SFlow] Re-detección de audio pedida.")

    @pyqtSlot()
    def _on_speak_toggle(self):
        """Double-tap Right ⌘: read the SELECTION (or clipboard) aloud; again = stop."""
        if self.speaker.is_speaking():
            self.speaker.stop()
            self._speak_timer.stop()
            self._voz_descartar()   # callar = también lo que venía en fila
            self.pill.set_state(PillWidget.STATE_IDLE)
            return

        # Capturar la selección simula Cmd+C y espera: va al hilo "sistema".
        self.io.submit(lambda: self.texto_para_leer.emit(get_selected_or_clipboard_text()),
                       "capturar selección")

    @pyqtSlot(str)
    def _on_texto_para_leer(self, text: str, voz: str | None = None):
        text = text.strip()
        if not text:
            msg = "Selecciona o copia un texto y toca dos veces ⌘ derecho."
            self.pill.setToolTip(msg)
            if self.tray is not None:
                self.tray.showMessage("SFlow", msg, QSystemTrayIcon.MessageIcon.Information, 4000)
            return

        if self.speaker.speak(text, voz):
            # 'fetching' → spinner; the poll flips to SPEAKING when audio starts
            self.pill.set_state(PillWidget.STATE_PROCESSING)
            self._speak_timer.start()

    def _check_speaking(self):
        """Follow the speaker's phase: spinner while fetching, waves while playing."""
        phase = self.speaker.phase
        if phase == "playing" and self.pill._state == PillWidget.STATE_PROCESSING:
            self.pill.set_state(PillWidget.STATE_SPEAKING)
        elif not self.speaker.is_speaking():
            self._speak_timer.stop()
            if self._voz_actual is not None:     # terminó una frase de Claude
                self._voz_terminado = max(self._voz_terminado, self._voz_actual)
                self._voz_actual = None
            if self.pill._state in (PillWidget.STATE_SPEAKING, PillWidget.STATE_PROCESSING):
                self.pill.set_state(PillWidget.STATE_IDLE)

    # -- resúmenes en voz de Claude Code (claude_hooks/voz.py → /voz) ----------

    @pyqtSlot(int, str, str)
    def _on_voz_de_claude(self, vid: int, text: str, voz: str = ""):
        if not self.voz_claude:
            print(f"[SFlow] voz de Claude · {len(text)} chars · silenciada desde el menú")
            self._voz_terminado = max(self._voz_terminado, vid)   # nadie debe esperarla
            return
        print(f"[SFlow] voz de Claude · {len(text)} chars")
        self._voz_fila.append((vid, text, voz))
        self._hablar_siguiente()
        if self._voz_fila:
            self._voz_timer.start()

    def _hablar_siguiente(self):
        """Dice el siguiente de la fila si SFlow está libre; si no, el timer reintenta."""
        if not self._voz_fila:
            self._voz_timer.stop()
            return
        ocupado = (self.recorder.is_recording or self.speaker.is_speaking()
                   or self.pill._state in (PillWidget.STATE_RECORDING, PillWidget.STATE_PROCESSING,
                                           PillWidget.STATE_DONE, PillWidget.STATE_ERROR))
        if ocupado:
            return
        vid, text, voz = self._voz_fila.pop(0)
        self._voz_actual = vid
        self._on_texto_para_leer(text, voz or None)
        if not self._voz_fila:
            self._voz_timer.stop()

    def _voz_descartar(self):
        """Vacía la fila: lo que venía (y lo que sonaba) cuenta como terminado."""
        ids = [f[0] for f in self._voz_fila] + ([self._voz_actual] if self._voz_actual else [])
        self._voz_fila.clear()
        self._voz_actual = None
        if ids:
            self._voz_terminado = max(self._voz_terminado, *ids)

    def voz_estado(self) -> dict:
        """Para GET /voz/estado (se lee desde el hilo de Flask: solo enteros)."""
        return {"terminado": self._voz_terminado}

    def set_voz_claude(self, activa: bool):
        """Menú de la barra: leer (o no) los resúmenes 🔊 de Claude. Se recuerda."""
        self.voz_claude = bool(activa)
        if not activa:
            self._voz_descartar()
        s = load_settings()
        s["voz_claude"] = self.voz_claude
        save_settings(s)

    def set_pill_size(self, px: int):
        """Cambia el tamaño del orbe flotante y lo recuerda entre reinicios."""
        self.pill.set_pill_size(px)
        s = load_settings()
        s["pill_size"] = int(px)
        save_settings(s)

    def set_reposo_vivo(self, activo: bool):
        """Menú: el orbe se mueve lento en reposo (~10 % de un núcleo, solo con el cargador) o se congela."""
        self.pill.set_reposo_vivo(activo)
        s = load_settings()
        s["reposo_vivo"] = bool(activo)
        save_settings(s)

    def set_orb_paleta(self, paleta: str):
        """Menú «Color del orbe»: cambia la paleta y la recuerda entre reinicios."""
        self.pill.set_paleta(paleta)
        s = load_settings()
        s["orb_paleta"] = paleta
        save_settings(s)

    def set_tts_voice(self, voice: str, nombre: str):
        """Menú "Voz de lectura": cambia la voz de LemonFox, la recuerda y saluda con
        ella (una voz inválida no da error en LemonFox: el saludo es la prueba de oído)."""
        self.speaker.voice = voice
        s = load_settings()
        s["tts_voice"] = voice
        save_settings(s)
        self._on_texto_para_leer(f"Hola, soy {nombre}. Desde ahora te leo yo.")

    def set_tts_speed(self, speed: float):
        """Change the read-aloud speed (menu bar) and remember it across restarts."""
        self.speaker.speed = speed
        s = load_settings()
        s["tts_speed"] = speed
        save_settings(s)

    # -- LemonFox usage meter (local — their API exposes no balance) --------

    @staticmethod
    def _cycle_start_str() -> str:
        """Billing-cycle anchor: the most recent LEMONFOX_CYCLE_DAY (the 22nd)."""
        import datetime as _dt
        today = _dt.date.today()
        if today.day >= LEMONFOX_CYCLE_DAY:
            start = today.replace(day=LEMONFOX_CYCLE_DAY)
        else:
            prev_month = today.replace(day=1) - _dt.timedelta(days=1)
            start = prev_month.replace(day=LEMONFOX_CYCLE_DAY)
        return start.isoformat()

    def _lemonfox_usage(self) -> tuple[int, str]:
        """(chars this cycle, cycle start). Resets automatically on the 22nd."""
        s = load_settings()
        cycle = self._cycle_start_str()
        if s.get("lemonfox_cycle") != cycle:
            # New cycle (or first run): seed only the cycle the meter was born in
            chars = LEMONFOX_SEED_CHARS if "lemonfox_cycle" not in s else 0
            s["lemonfox_cycle"] = cycle
            s["lemonfox_chars"] = chars
            save_settings(s)
        return int(s.get("lemonfox_chars", 0)), cycle

    @pyqtSlot(int)
    def _on_lemonfox_chars(self, n: int):
        chars, _ = self._lemonfox_usage()  # ensures cycle is current
        s = load_settings()
        s["lemonfox_chars"] = chars + n
        save_settings(s)

    def lemonfox_status_text(self) -> str:
        chars, _ = self._lemonfox_usage()
        pct = chars / LEMONFOX_CHARS_PER_CYCLE * 100
        return f"LemonFox: {chars:,} chars (~{pct:.1f}% de 2M)"

    @pyqtSlot(str)
    def _on_tts_fallback(self, msg: str):
        if self.tray is not None:
            self.tray.showMessage("SFlow", msg, QSystemTrayIcon.MessageIcon.Warning, 4000)

    def _transcribe_worker(self, wav_buffer, duration, destino, rms=0.0):
        try:
            text = self.transcriber.transcribe(wav_buffer)
            if text:
                meta = {"destino": destino, "motor": self.transcriber.last_engine_used,
                        "seg": self.transcriber.last_seconds, "rms": rms}
                self.transcription_done.emit(text, duration, meta)
            else:
                self.transcription_error.emit("No speech detected")
        except Exception as e:
            self.transcription_error.emit(str(e))

    @pyqtSlot(str, float, object)
    def _on_transcription_done(self, text: str, duration: float, meta: dict):
        trace("transcrito")
        self.db.insert(text=text, duration_seconds=duration)   # sqlite vive en este hilo
        # Pegar espera el foco (hasta 0.8 s) y osascript: va al hilo "sistema".
        # El orbe sigue "pensando" hasta que llegue la señal `pegado`.
        self.io.submit(lambda: self._pegar(text, duration, meta), "pegar texto")

    def _pegar(self, text: str, duration: float, meta: dict):
        """Hilo "sistema". Deja en el log una línea por dictado: es lo que se
        mira cuando SFlow 'se siente lenta'."""
        t0 = time.monotonic()
        ok = paste_text(text, meta["destino"])
        pegar_s = time.monotonic() - t0
        print(f"[SFlow] dictado · audio {duration:.1f} s · {meta['motor']} {meta['seg']:.1f} s"
              f" · pegado {pegar_s:.2f} s · {len(text)} chars · mic {meta.get('rms', 0):.3f}"
              + ("" if ok else " · NO pegado"))
        self.pegado.emit(ok)

    def _consultar_cargador(self):
        self.io.submit(lambda: self.cargador.emit(con_cargador()), "consultar cargador")

    @pyqtSlot(bool)
    def _on_cargador(self, conectado: bool):
        if conectado != self.pill.con_cargador:
            print(f"[SFlow] energía: {'cargador' if conectado else 'batería'} → reposo "
                  f"{'vivo' if conectado else 'congelado'}", flush=True)
        self.pill.set_cargador(conectado)

    @pyqtSlot(bool)
    def _on_pegado(self, pasted: bool):
        self.pill.set_state(PillWidget.STATE_DONE)
        if not pasted and self.tray is not None:
            # Focus could not be confirmed: better in the clipboard than pasted
            # into the wrong window (e.g. the other Chrome profile).
            self.tray.showMessage(
                "SFlow", "No pude volver a la ventana original. El texto está "
                         "en el portapapeles: pégalo con Cmd+V.",
                QSystemTrayIcon.MessageIcon.Warning, 5000)

    @pyqtSlot(str)
    def _on_transcription_error(self, error: str):
        self.pill.set_state(PillWidget.STATE_ERROR)
        friendly = _friendly_error(error)
        print(f"[SFlow] Transcripción falló: {error}")
        self.pill.setToolTip(friendly)
        # Silence ("no speech") is expected — don't nag with a notification
        if self.tray is not None and "no speech" not in error.lower():
            self.tray.showMessage(
                "SFlow", friendly,
                QSystemTrayIcon.MessageIcon.Warning, 6000,
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
_lock_fd = None


def _instancia_unica() -> bool:
    """Un solo SFlow a la vez. El candado (flock) vive mientras el proceso viva.

    Defensa de fondo contra cualquier relanzamiento accidental: si otra
    instancia ya tiene el candado, esta sale sin hacer nada. Sin esto, un
    bucle de relanzamiento acaba con la RAM de la máquina (pasó el 1-sep)."""
    global _lock_fd
    try:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        # "a+", no "w": abrir en "w" vaciaría el pid que escribió la instancia viva
        _lock_fd = open(os.path.join(APP_DATA_DIR, "sflow.lock"), "a+")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.seek(0); _lock_fd.truncate()
        _lock_fd.write(str(os.getpid())); _lock_fd.flush()
        return True
    except OSError:
        return False


def _pid_con_candado() -> int | None:
    try:
        with open(os.path.join(APP_DATA_DIR, "sflow.lock")) as f:
            return int(f.read().strip() or 0) or None
    except (OSError, ValueError):
        return None


def _tomar_control_si_colgada() -> bool:
    """Abriste SFlow desde Spotlight y ya hay otra. Si la otra está congelada
    (su latido lleva >15 s quieto), la cierra y toma su lugar. Es el botón de
    aborto manual: no hace falta terminal ni Monitor de Actividad."""
    pid = _pid_con_candado()
    if not pid or pid == os.getpid() or not instancia_colgada(pid):
        return False
    _log_anexar(f"[SFlow] La instancia {pid} no responde: la cierro y tomo su lugar.")
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    for _ in range(50):   # hasta 5 s a que el sistema suelte su candado
        time.sleep(0.1)
        if _instancia_unica():
            return True
    return False


def _log_anexar(msg: str):
    """Escribe en el log SIN vaciarlo (para la instancia que no se queda)."""
    print(msg)
    if getattr(sys, "frozen", False):
        try:
            with open(os.path.join(APP_DATA_DIR, "sflow.log"), "a") as f:
                f.write(msg + "\n")
        except OSError:
            pass


def _log_a_archivo():
    """En el .app no hay terminal: stdout/stderr van a Application Support/SFlow/sflow.log
    (línea a línea, se trunca al arrancar). Es lo que se lee cuando algo falla."""
    if not getattr(sys, "frozen", False):
        return
    try:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        ruta = os.path.join(APP_DATA_DIR, "sflow.log")
        # El log de la sesión anterior se guarda aparte: si el guardián
        # reinició la app, ahí están las pilas de hilos del cuelgue.
        if os.path.exists(ruta):
            os.replace(ruta, os.path.join(APP_DATA_DIR, "sflow.anterior.log"))
        f = open(ruta, "w", buffering=1)
        sys.stdout = sys.stderr = f
        print(f"[SFlow] arranque {time.strftime('%Y-%m-%d %H:%M:%S')} · pid {os.getpid()} · {sys.executable}")
    except OSError:
        pass


_actividad = None


def _sin_app_nap():
    """macOS "adormece" (App Nap) las apps de fondo: sus temporizadores se
    retrasan y sus hilos bajan de prioridad. SFlow vive en la barra, así que
    es candidata, y eso se siente como un dictado lento. Además el guardián
    confundiría una siesta con un cuelgue. Esta actividad lo impide (también
    va NSAppSleepDisabled en el Info.plist). No impide que la Mac duerma."""
    global _actividad
    try:
        from Foundation import NSProcessInfo, NSActivityUserInitiatedAllowingIdleSystemSleep
        _actividad = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            NSActivityUserInitiatedAllowingIdleSystemSleep, "SFlow espera atajos de dictado")
    except Exception as e:
        print(f"[SFlow] no pude desactivar App Nap: {e}")


def _abrir_puerta_voz(sflow, port: int):
    """Deja en APP_DATA_DIR/voz.json el puerto y una llave nueva de esta sesión:
    el gancho de Claude Code (claude_hooks/voz.py) los lee para hablarle a /voz."""
    token = secrets.token_hex(16)
    registrar_voz(sflow.voz_de_claude.emit, sflow.voz_estado, token)
    ruta = os.path.join(APP_DATA_DIR, "voz.json")
    try:
        fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"puerto": port, "token": token}, f)
    except OSError as e:
        print(f"[SFlow] No pude escribir voz.json: {e}")


def main():
    # Primero el candado y DESPUÉS el log: la instancia que no se queda no debe
    # vaciar el log de la que sí (ahí puede estar el diagnóstico de un cuelgue).
    if not _instancia_unica() and not _tomar_control_si_colgada():
        _log_anexar("[SFlow] Ya hay una instancia corriendo. Saliendo.")
        sys.exit(0)
    _log_a_archivo()
    _sin_app_nap()
    app = QApplication(sys.argv)
    app.setApplicationName("SFlow")
    app.setQuitOnLastWindowClosed(False)

    # Allow Ctrl+C to kill the app
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # First-run: ask for API key if missing (BEFORE hiding from Dock so dialog is visible)
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        dialog = FirstRunDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            sys.exit(0)

    # Hide from Dock AFTER first-run dialog — menu bar only
    try:
        import AppKit
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    except Exception:
        pass  # Non-critical: just means Dock icon stays

    # Start web dashboard
    port = start_web_server()

    # Request Accessibility permission (shows macOS prompt if not granted)
    _ensure_accessibility()

    # Start the app
    sflow = SFlowApp()
    sflow.start()
    _abrir_puerta_voz(sflow, port)

    # System tray icon
    tray = _setup_tray(app, port, sflow=sflow)
    sflow.tray = tray  # let the app show error notifications through it
    _refresh_launch_agent()  # si "Iniciar con macOS" apuntaba al dev, ahora al .app

    sys.exit(app.exec())


if __name__ == "__main__":
    # (el desvío del ayudante de multiprocessing ya corrió arriba, antes de los imports) Dentro del .app, sys.executable es la
    # propia SFlow: cualquier ayudante de multiprocessing (el resource_tracker que
    # arranca tqdm, por ejemplo) relanzaría la app completa. freeze_support()
    # reconoce esos argumentos, ejecuta el ayudante y sale. Sin esto, el 1-sep el
    # bundle se clonó sin fin y dejó la Mac sin RAM.
    multiprocessing.freeze_support()
    main()
