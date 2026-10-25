"""El guardián de SFlow: que la app nunca se quede congelada sin salida.

Tres piezas:

- `SerialWorker`: un hilo con su cola de trabajos, en orden. Todo lo que puede
  bloquearse fuera de nuestro control (abrir/cerrar el micrófono en CoreAudio,
  osascript para pegar, capturar la selección) corre aquí y NUNCA en el hilo
  principal, que es el que mueve el orbe, las teclas y el menú.

- `Guardian`: un vigilante que cada segundo comprueba que el hilo principal
  late (un QTimer llama a `latido()`) y que ningún SerialWorker lleva
  demasiado en un mismo trabajo. Si algo se atasca, deja las pilas de todos
  los hilos en el log y relanza SFlow.

- `relanzar()`: el reinicio con freno. Máximo 3 reinicios AUTOMÁTICOS en 10
  minutos: el 1-sep una app que se relanzaba sola se volvió una bomba de
  procesos, y esto no puede volver a pasar.

Motivo (26-sep): SFlow se congeló en `AudioOutputUnitStop`, un interbloqueo
entre CoreAudio y PortAudio al cerrar el micrófono. Como eso corría en el hilo
principal, se murieron orbe, teclas y menú a la vez, y solo quedó matarla.
"""
import faulthandler
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time

from config import APP_DATA_DIR

RUTA_LATIDO = os.path.join(APP_DATA_DIR, "sflow.latido")
_RUTA_REINICIOS = os.path.join(APP_DATA_DIR, "reinicios.json")
_MAX_REINICIOS_AUTO = 3
_VENTANA_REINICIOS_S = 600


class SerialWorker:
    """Un hilo, una cola, trabajos en orden. Sabe cuánto lleva el trabajo actual."""

    def __init__(self, nombre: str):
        self.nombre = nombre
        self._cola: queue.Queue = queue.Queue()
        self._ocupado_desde: float | None = None
        self._trabajo = ""
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name=f"sflow-{nombre}")
        self._hilo.start()

    def submit(self, fn, que: str):
        self._cola.put((fn, que))

    def _bucle(self):
        while True:
            fn, que = self._cola.get()
            self._trabajo = que
            self._ocupado_desde = time.monotonic()
            try:
                fn()
            except Exception as e:
                print(f"[SFlow] {self.nombre}: '{que}' falló: {e}")
            finally:
                self._ocupado_desde = None

    def atascado(self) -> tuple[float, str]:
        """(segundos en el trabajo actual, cuál). (0, '') si está libre."""
        desde = self._ocupado_desde
        if desde is None:
            return 0.0, ""
        return time.monotonic() - desde, self._trabajo


def comando_arranque(manual: bool) -> list[str]:
    """Cómo volver a abrir SFlow. [] si no hay forma segura.

    En el .app, solo desde /Applications (misma regla que el LaunchAgent: nada
    se relanza desde dist/ ni desde la Papelera). En dev, solo si lo pide el
    usuario desde el menú: un relanzamiento automático en dev se llevaría la
    app lejos de la terminal donde la estás mirando."""
    if getattr(sys, "frozen", False):
        bundle = os.path.abspath(os.path.join(sys.executable, "..", "..", ".."))
        if bundle.startswith("/Applications/"):
            return ["/usr/bin/open", "-n", "-a", bundle]
        return []
    if manual:
        return [sys.executable, os.path.abspath(sys.argv[0])]
    return []


def relanzar(motivo: str, automatico: bool):
    """Cierra este proceso y abre uno nuevo 1.5 s después. No regresa.

    El nuevo toma el candado de instancia única cuando este ya murió, así que
    nunca hay dos. Los automáticos tienen freno: si ya van 3 en 10 minutos,
    SFlow se cierra sin volver a abrirse y lo dice en el log."""
    ahora = time.time()
    try:
        with open(_RUTA_REINICIOS) as f:
            previos = [t for t in json.load(f) if ahora - t < _VENTANA_REINICIOS_S]
    except (OSError, ValueError, TypeError):
        previos = []

    cmd = comando_arranque(manual=not automatico)
    if automatico and len(previos) >= _MAX_REINICIOS_AUTO:
        print(f"[SFlow] GUARDIÁN: ya van {len(previos)} reinicios automáticos en 10 min. "
              f"No relanzo, para no entrar en bucle. Ábrela a mano desde Spotlight.")
        cmd = []
    else:
        try:
            with open(_RUTA_REINICIOS, "w") as f:
                json.dump(previos + [ahora], f)
        except OSError:
            pass

    if cmd:
        print(f"[SFlow] Reiniciando ({motivo}).")
        # /bin/sh espera a que este proceso muera y suelte el candado.
        subprocess.Popen(
            ["/bin/sh", "-c", f"sleep 1.5; exec {shlex.join(cmd)}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    else:
        print(f"[SFlow] Cerrando ({motivo}) sin relanzar.")
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(3)   # inmediato, aunque otro hilo esté atascado en CoreAudio


class Guardian:
    """Vigila el latido del hilo principal y los SerialWorker."""

    ESPERA_INICIAL_S = 10.0   # al arrancar, deja que todo se acomode
    SUENO_S = 3.0             # un hueco mayor en el propio vigilante = la Mac durmió

    def __init__(self, limite_principal: float = 10.0):
        self.limite_principal = limite_principal
        self._vigilados: list[tuple[str, SerialWorker, float]] = []
        self._latido = time.monotonic()
        self._n = 0

    def vigilar(self, nombre: str, worker: SerialWorker, limite_s: float):
        self._vigilados.append((nombre, worker, limite_s))

    def latido(self):
        """Lo llama un QTimer del hilo principal cada segundo."""
        self._latido = time.monotonic()
        self._n += 1
        if self._n % 2 == 0:   # el archivo es para la otra instancia (toma de control)
            try:
                with open(RUTA_LATIDO, "a"):
                    pass
                os.utime(RUTA_LATIDO, None)
            except OSError:
                pass

    def start(self):
        threading.Thread(target=self._bucle, daemon=True, name="sflow-guardian").start()

    def _bucle(self):
        ultimo = time.monotonic()
        gracia = ultimo + self.ESPERA_INICIAL_S
        while True:
            time.sleep(1.0)
            ahora = time.monotonic()
            if ahora - ultimo > self.SUENO_S:
                # La Mac (o el proceso) estuvo dormida: nadie latió porque nadie
                # corría. No es un cuelgue; se da margen para que todo despierte.
                gracia = ahora + 15.0
                self._latido = ahora
            ultimo = ahora
            if ahora < gracia:
                continue

            motivo = None
            quieto = ahora - self._latido
            if quieto > self.limite_principal:
                motivo = f"el hilo principal no responde hace {quieto:.0f} s"
            for nombre, worker, limite in self._vigilados:
                t, que = worker.atascado()
                if t > limite:
                    motivo = f"el hilo de {nombre} lleva {t:.0f} s atascado en '{que}'"
            if motivo:
                self._rescatar(motivo)
                return

    def _rescatar(self, motivo: str):
        print(f"[SFlow] GUARDIÁN: {motivo}. Pilas de todos los hilos para el diagnóstico:")
        try:
            sys.stderr.flush()
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception:
            pass
        relanzar(motivo, automatico=True)


def instancia_colgada(pid: int, umbral_s: float = 15.0) -> bool:
    """¿La instancia `pid` que tiene el candado está congelada?

    Se usa al abrir SFlow desde Spotlight con otra ya corriendo: si su latido
    está viejo, la nueva la cierra y toma su lugar. Solo se toca un proceso
    que de verdad sea SFlow (el .app o el main.py de dev)."""
    try:
        edad = time.time() - os.path.getmtime(RUTA_LATIDO)
    except OSError:
        return False
    if edad < umbral_s:
        return False
    try:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:
        return False
    return "SFlow" in cmd or cmd.endswith("main.py")
