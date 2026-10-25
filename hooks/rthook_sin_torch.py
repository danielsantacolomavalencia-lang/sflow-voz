"""Runtime hook de PyInstaller: mlx-whisper importa numba y scipy en timing.py,
pero solo los usa para marcas de tiempo por palabra (word_timestamps), que
SFlow no pide. torch solo vive en torch_whisper.py (conversión de modelos).

Excluir los tres del bundle ahorra ~780 MB. Estos dos módulos ficticios
satisfacen el `import` y hacen que `@numba.jit` sea un decorador vacío.
Si algún día se piden word_timestamps, medfilt avisa en vez de fallar raro.
SOLO corre dentro del .app: en dev el venv trae los paquetes reales."""
import sys
import types


def _modulo(nombre, **attrs):
    m = types.ModuleType(nombre)
    m.__dict__.update(attrs)
    sys.modules[nombre] = m
    return m


def _jit(*args, **kwargs):
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]            # @numba.jit sin paréntesis
    return lambda f: f            # @numba.jit(nopython=True, ...)


def _medfilt(*args, **kwargs):
    raise NotImplementedError("scipy no va en el bundle de SFlow (word_timestamps no soportado)")


if "numba" not in sys.modules:
    _modulo("numba", jit=_jit, njit=_jit)
if "scipy" not in sys.modules:
    scipy = _modulo("scipy")
    scipy.signal = _modulo("scipy.signal", medfilt=_medfilt)
