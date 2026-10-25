import queue
import time
from ctypes import c_void_p
import AppKit
import numpy as np
import objc
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QPen, QImage, QRadialGradient
from ui.orb import OrbEngine, VoiceSimulator, render_masked, PALETA_DEFAULT  # el orbe de dos capas del pill
from config import (
    PILL_HEIGHT,
    ORB_FILL,
    PILL_OPACITY,
    PILL_OPACITY_CORE,
    PILL_CORE_STOP,
    PILL_BG_RGB,
    PILL_CORNER_RADIUS,
    LOGO_SIZE,
    ORB_FPS,
    ORB_IDLE_FREEZE_S,
    ORB_REPOSO_FPS,
    ORB_REPOSO_LENTO,
)

# Mic RMS → 0..1 orb level. Normal speech lands around 0.03-0.15 RMS.
_MIC_GAIN = 7.0
_MIC_DECAY = 0.55  # smoothing so the orb breathes instead of flickering


class PillWidget(QWidget):
    """Floating pill: the two-layer orb, coloured by what SFlow is doing."""

    STATE_IDLE = "idle"
    STATE_RECORDING = "recording"
    STATE_PROCESSING = "processing"
    STATE_DONE = "done"
    STATE_ERROR = "error"
    STATE_SPEAKING = "speaking"

    # Estado de SFlow → estado del orbe. El motor trae dentro la piel, el color
    # del corazón y las perillas de movimiento.
    _LOOKS = {
        STATE_IDLE:       "reposo",
        STATE_RECORDING:  "escucha",
        STATE_PROCESSING: "pensando",
        STATE_DONE:       "respondiendo",
        STATE_ERROR:      "error",
        STATE_SPEAKING:   "respondiendo",
    }

    def __init__(self, size_px: int | None = None, paleta: str | None = None):
        super().__init__()
        self._size = int(size_px or PILL_HEIGHT)
        self._logo = int(self._size * ORB_FILL)
        self._state = self.STATE_IDLE
        self._paleta = paleta or PALETA_DEFAULT   # menú «Color del orbe»
        self._drag_pos = None
        self._bg_core = QColor(*PILL_BG_RGB, int(255 * PILL_OPACITY_CORE))
        self._bg_edge = QColor(*PILL_BG_RGB, int(255 * PILL_OPACITY))

        # El motor hace su propio crossfade entre estados (mix), así que el pill
        # ya no lleva paletas: solo le dice en qué estado está.
        # lado_px = lado del búfer: el motor se calibra por tamaño.
        self._orb = self._crear_motor()
        self._sim = VoiceSimulator()
        self._level = 0.0
        self._audio_queue: queue.Queue | None = None
        self._voz_real = None   # callable → volumen real de la voz de SFlow (Speaker.nivel_voz)

        self._t0 = time.monotonic()
        self._t_orbe = 0.0        # el reloj del orbe (en reposo vivo avanza más lento)
        self.reposo_vivo = True   # sin actividad: movimiento lento en vez de congelarse
        self.con_cargador = True  # con batería el reposo se congela (main.py lo consulta cada 30 s)
        self._lento = False
        self._last_frame = self._t0
        self._last_activity = self._t0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self._size, self._size)  # siempre redondo: el color dice el estado

        screen = QApplication.primaryScreen()
        self._dpr = screen.devicePixelRatio() if screen else 2.0
        # Buffer in PHYSICAL pixels. Do NOT set its devicePixelRatio: that would
        # make the painter inside it scale by dpr as well, drawing the orb at
        # double size (only a quarter of it lands in the buffer). paintEvent
        # scales it down to LOGO_SIZE points, which on retina is a 1:1 mapping.
        px = int(self._logo * self._dpr)
        self._buf = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)

        self._anim_timer = QTimer()
        self._anim_timer.setInterval(max(8, int(1000 / ORB_FPS)))
        self._anim_timer.timeout.connect(self._tick)

        self._done_timer = QTimer()
        self._done_timer.setSingleShot(True)
        self._done_timer.timeout.connect(lambda: self.set_state(self.STATE_IDLE))

        self._position_on_screen()
        self._render_orb(0.0)  # first frame ready before the window shows

    # -- audio ------------------------------------------------------------

    def set_audio_queue(self, q: queue.Queue):
        """Mic chunks from the recorder — drives the orb while listening."""
        self._audio_queue = q

    def set_fuente_voz(self, fn):
        """Volumen real de lo que dice SFlow (0..1, o None si no hay cómo medirlo)."""
        self._voz_real = fn

    def _read_mic_level(self) -> float:
        """Latest RMS from the recorder queue, smoothed. 0 if nothing pending."""
        if self._audio_queue is None:
            return 0.0
        latest = None
        while True:
            try:
                latest = self._audio_queue.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return self._level * _MIC_DECAY
        chunk = latest[:, 0] if latest.ndim > 1 else latest
        rms = float(np.sqrt(np.mean((chunk.astype(np.float32) / 32768.0) ** 2)))
        target = min(1.0, rms * _MIC_GAIN)
        # rise fast, fall smooth
        return target if target > self._level else self._level * _MIC_DECAY + target * (1 - _MIC_DECAY)

    # -- placement / native window ----------------------------------------

    def _position_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self._size // 2, geo.bottom() - 4 - self._size)

    def _setup_native_macos(self):
        """Float above everything without stealing focus (same as Spotlight)."""
        ns_view = objc.objc_object(c_void_p=c_void_p(self.winId().__int__()))
        ns_window = ns_view.window()
        ns_window.setLevel_(AppKit.NSFloatingWindowLevel)
        ns_window.setStyleMask_(ns_window.styleMask() | AppKit.NSWindowStyleMaskNonactivatingPanel)
        ns_window.setHidesOnDeactivate_(False)
        ns_window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self._setup_native_macos()
        except Exception as e:
            print(f"Warning: native macOS setup failed: {e}")
        self._wake()

    # -- state ------------------------------------------------------------

    def _crear_motor(self):
        """El orbe, a la medida del pill y con la paleta elegida."""
        return OrbEngine(lado_px=self._logo, paleta=self._paleta)

    def _look(self, state: str) -> str:
        """Estado del pill → estado del motor."""
        return getattr(self._orb, "LOOKS", self._LOOKS)[state]

    def _rehacer_motor(self):
        """Motor nuevo ya asentado en el estado actual, sin fundido."""
        self._orb = self._crear_motor()
        self._orb.estado_a = self._orb.estado_b = self._look(self._state)
        self._orb.mix = 1.0

    def set_state(self, state: str):
        if state not in self._LOOKS:
            return
        self._state = state
        self._orb.set_estado(self._look(state))
        self._done_timer.stop()
        if state == self.STATE_DONE:
            self._done_timer.start(800)
        elif state == self.STATE_ERROR:
            self._done_timer.start(1200)
        self._wake()

    def set_pill_size(self, px: int):
        """Cambia el tamaño del pill en vivo (menú de la barra).

        El orbe se reconstruye porque el motor calibra su nube por tamaño:
        el número de partículas y el grosor del punto dependen del lado."""
        px = int(px)
        if px == self._size:
            return
        centro = self.geometry().center()
        self._size = px
        self._logo = int(px * ORB_FILL)
        self.setFixedSize(px, px)
        buf_px = int(self._logo * self._dpr)
        self._buf = QImage(buf_px, buf_px, QImage.Format.Format_ARGB32_Premultiplied)
        self._rehacer_motor()
        self.move(centro.x() - px // 2, centro.y() - px // 2)
        self._render_orb(self._t_orbe)
        self._wake()
        self.update()

    def set_paleta(self, paleta: str):
        """Menú «Color del orbe»: cambia los colores en vivo, sin reiniciar."""
        if paleta == self._paleta:
            return
        self._paleta = paleta
        self._rehacer_motor()
        self._render_orb(self._t_orbe)
        self._wake()
        self.update()

    def _wake(self):
        """Resume animating (and restart the idle-freeze countdown)."""
        self._last_activity = time.monotonic()
        if self._lento:                      # de reposo vivo a velocidad completa, al instante
            self._lento = False
            self._anim_timer.setInterval(max(8, int(1000 / ORB_FPS)))
        if not self._anim_timer.isActive():
            self._last_frame = time.monotonic()
            self._anim_timer.start()

    # -- animation --------------------------------------------------------

    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, now - self._last_frame)   # 0.1: deja pasar los cuadros de 12 cps del reposo vivo
        self._last_frame = now
        if self._lento:
            dt *= ORB_REPOSO_LENTO           # reposo vivo: el mismo movimiento, más lento
        self._t_orbe += dt
        t = self._t_orbe

        if self._state == self.STATE_RECORDING:
            self._level = self._read_mic_level()      # tu voz mueve el orbe
            self._sim.update(dt, False)
        elif self._state == self.STATE_SPEAKING:
            # El volumen REAL del audio de la voz (medido antes de reproducirlo). Si no
            # hay cómo medirlo (voz de respaldo `say`), el simulador con ritmo de palabras.
            real = self._voz_real() if self._voz_real else None
            self._level = real if real is not None else self._sim.update(dt, True)
        else:
            self._level = self._sim.update(dt, False)

        self._orb.avanzar(dt, self._level)
        self._render_orb(t)
        self.update()

        # Batería: en reposo y ya asentado → reposo vivo (lento, 12 cps), o congelado si está
        # apagado o si la Mac va con batería.
        if (self._state == self.STATE_IDLE and self._orb.mix >= 1.0
                and now - self._last_activity > ORB_IDLE_FREEZE_S):
            if not (self.reposo_vivo and self.con_cargador):
                self._anim_timer.stop()
            elif not self._lento:
                self._lento = True
                self._anim_timer.setInterval(max(8, int(1000 / ORB_REPOSO_FPS)))

    def set_reposo_vivo(self, activo: bool):
        """Menú "Movimiento en reposo": lento y vivo, o congelado (ahorra batería)."""
        self.reposo_vivo = bool(activo)
        self._wake()

    def set_cargador(self, conectado: bool):
        """Con batería, el reposo se congela; al conectar el cargador vuelve a moverse."""
        if bool(conectado) != self.con_cargador:
            self.con_cargador = bool(conectado)
            if self.con_cargador:
                self._wake()          # desconectar no hace falta: el siguiente cuadro lento lo congela

    def _render_orb(self, t: float):
        render_masked(self._orb, self._buf, t, self._level, self._dpr)

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        # Fondo sólido hasta PILL_CORE_STOP del radio, baja a PILL_OPACITY en el
        # borde y se recorta en círculo. Sin pen: el antialiasing no deja línea.
        bg = QRadialGradient(w / 2.0, h / 2.0, w / 2.0)
        bg.setColorAt(0.0, self._bg_core)
        bg.setColorAt(PILL_CORE_STOP, self._bg_core)
        bg.setColorAt(1.0, self._bg_edge)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(QPointF(w / 2.0, h / 2.0), w / 2.0, h / 2.0)

        off = (h - self._logo) / 2.0
        painter.drawImage(QRectF(off, off, self._logo, self._logo), self._buf)
        painter.end()

    # -- drag -------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._wake()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
