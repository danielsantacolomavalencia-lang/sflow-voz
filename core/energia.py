"""¿La Mac está conectada al cargador? (23-oct)

El "reposo vivo" del orbe cuesta ~10 % de un núcleo: con el cargador da igual, con
batería no vale la pena. Esto pregunta a macOS (IOKit, lo mismo que el ícono de batería)
de dónde viene la energía. Una Mac de escritorio (Mac mini) responde siempre "AC Power".

Corre en el hilo "sistema", nunca en el principal: es una llamada al sistema que no
controlamos. Ante cualquier error responde True (el orbe sigue como estaba).
"""
import ctypes

_UTF8 = 0x08000100   # kCFStringEncodingUTF8

try:
    _iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
    _cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    _iokit.IOPSCopyPowerSourcesInfo.restype = ctypes.c_void_p
    _iokit.IOPSGetProvidingPowerSourceType.restype = ctypes.c_void_p
    _iokit.IOPSGetProvidingPowerSourceType.argtypes = [ctypes.c_void_p]
    _cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    _cf.CFStringGetCString.restype = ctypes.c_bool
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
except Exception:
    _iokit = None


def fuente_de_energia() -> str:
    """"AC Power", "Battery Power", "UPS Power" o "" si no se pudo saber."""
    if _iokit is None:
        return ""
    info = _iokit.IOPSCopyPowerSourcesInfo()
    if not info:
        return ""
    try:
        tipo = _iokit.IOPSGetProvidingPowerSourceType(info)   # constante del sistema: no se libera
        buf = ctypes.create_string_buffer(64)
        if tipo and _cf.CFStringGetCString(tipo, buf, len(buf), _UTF8):
            return buf.value.decode()
        return ""
    finally:
        _cf.CFRelease(info)


def con_cargador() -> bool:
    try:
        return fuente_de_energia() != "Battery Power"
    except Exception:
        return True
