"""La voz que viaja al celular (complemento de Arbrain).

Si usas Arbrain desde el celular (iPhone o iPad) y tu agente de Arbrain te habla con la voz directa (`decir.py`, skill
modo-voz), la frase suena en el celular y no en la Mac. Así no tienes que estar frente a la computadora para oírlo.

Cómo decide, sin configurar nada:
  · Solo para agentes que corren DENTRO de Arbrain: traen `ARBRAIN_CONFIG_DIR` (o `ARBRAIN_HOME`) en su entorno. Claude Code en tu
    terminal (fuera de Arbrain) habla siempre en la Mac: en el celular no hay quién diga su frase.
  · El chat de Arbrain en el celular escribe la hora de tu último toque en `<ARBRAIN_CONFIG_DIR>/telefono-activo.json`
    (complemento «voz al celular» de Arbrain). Si ese toque es más reciente que el último uso del teclado o el mouse de
    esta Mac (y de hace menos de 15 minutos), la frase va al celular.
  · Sin ese archivo (Arbrain sin el complemento) o con el celular en segundo plano, todo sigue como siempre: habla la Mac.

`decir.py` deja la frase en su salida con la marca `VOZ_EN_TELEFONO: …`; el chat de Arbrain en el celular la ve y la
dice con tu voz de LemonFox (o la del celular, si no tienes llave). Las Macs se callan.
Python 3.9 del sistema, solo biblioteca estándar. Nunca lanza.
"""
import json
import os
import re
import subprocess
import time

TELEFONO = "telefono"
# Si el celular lleva más de esto sin tocarse, ya no estás ahí.
SIN_USO_MAX_S = 15 * 60


def _archivo():
    base = os.environ.get("ARBRAIN_CONFIG_DIR", "").strip()
    if not base and os.environ.get("ARBRAIN_HOME", "").strip():
        base = os.path.expanduser("~/.config/arbrain")   # Arbrain corriendo desde su carpeta (sin la app): su carpeta de siempre
    return os.path.join(base, "telefono-activo.json") if base else ""


def telefono_sin_uso():
    """Segundos desde tu último toque en el celular (negativo si el Orbe de Arbrain reservó la voz), o None."""
    ruta = _archivo()
    if not ruta:
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            t = float(json.load(f).get("t", 0)) / 1000
        return time.time() - t if t > 0 else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def mac_sin_uso():
    """Segundos desde el último uso del teclado o el mouse de esta Mac (-1 si no se sabe)."""
    try:
        o = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r'"HIDIdleTime" = (\d+)', o)
        return int(m.group(1)) / 1e9 if m else -1.0
    except (OSError, ValueError, subprocess.SubprocessError):
        return -1.0


_decision = None  # cada gancho y cada decir.py es un proceso nuevo: se decide una vez por proceso


def en_el_celular():
    """True si la frase debe sonar en el celular y no en esta Mac."""
    global _decision
    if _decision is not None:
        return _decision
    tel = telefono_sin_uso()
    if tel is None or tel >= SIN_USO_MAX_S:
        _decision = False
        return _decision
    mac = mac_sin_uso()
    _decision = mac < 0 or tel < mac
    return _decision
