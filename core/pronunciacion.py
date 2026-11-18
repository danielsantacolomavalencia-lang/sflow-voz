"""Diccionario de pronunciación de la voz (LemonFox "dora" y `say` de respaldo) — 30-sep.

LemonFox no aprende: SFlow cambia cada palabra por su escritura "fonética" justo
antes de mandarla a la voz ("Groq" → "grok"). Aplica a todo lo que se lee en voz
alta: la lectura con doble ⌘ y los resúmenes 🔊 de Claude. No toca los dictados
(esos tienen su propia lista, STT_CORRECTIONS).

El archivo es APP_DATA_DIR/pronunciacion.json, un JSON plano {"palabra": "escritura"}
(las claves que empiezan con "_" son notas y se ignoran). SFlow lo relee cuando
cambia, así que sumar palabras NO requiere reinstalar. En dev, APP_DATA_DIR es la
raíz del repo: el pronunciacion.json del repo es la copia versionada.
Se administra con `herramientas/pronunciacion.py` (probar / sumar / quitar / lista).

Reglas: palabra completa (no toca "Groqueta"), sin distinguir mayúsculas, la más
larga primero ("LemonFox" antes que "Lemon") y en UNA pasada: lo que ya se
reemplazó no se vuelve a reemplazar.
Siglas: una clave escrita TODA en mayúsculas ("TI", "IT", "USD") solo cambia
cuando el texto la trae en mayúsculas — "el área de TI" sí, "para ti" no.
"""
import json
import os
import re
import threading

from config import APP_DATA_DIR

RUTA = os.path.join(APP_DATA_DIR, "pronunciacion.json")
_cache = {"mtime": None, "patron": None, "mapa": {}}
_lock = threading.Lock()


def _es_sigla(clave: str) -> bool:
    """"TI", "USD": toda en mayúsculas → se busca tal cual, no sin distinguir mayúsculas."""
    return clave.isupper()


def _cargar():
    """(patrón, mapa) al día con el archivo. Si el archivo se rompe, queda el anterior."""
    try:
        mtime = os.path.getmtime(RUTA)
    except OSError:
        return None, {}
    with _lock:
        if _cache["mtime"] != mtime:
            try:
                with open(RUTA, encoding="utf-8") as f:
                    datos = json.load(f)
                mapa = {(k.strip() if _es_sigla(k.strip()) else k.strip().lower()): v
                        for k, v in datos.items()
                        if isinstance(v, str) and k.strip() and not k.startswith("_")}
                # La más larga primero; a igual largo, la sigla antes que la otra.
                claves = sorted(mapa, key=lambda k: (-len(k), not _es_sigla(k)))
                partes = (re.escape(k) if _es_sigla(k) else f"(?i:{re.escape(k)})" for k in claves)
                patron = (re.compile(r"(?<!\w)(" + "|".join(partes) + r")(?!\w)")
                          if claves else None)
                _cache.update(mtime=mtime, patron=patron, mapa=mapa)
            except (OSError, ValueError, AttributeError) as e:
                print(f"[SFlow] pronunciacion.json no se pudo leer ({e}); sigo con el anterior.")
                _cache["mtime"] = mtime
        return _cache["patron"], _cache["mapa"]


def aplicar(texto: str) -> str:
    """El texto con cada palabra del diccionario cambiada por su escritura para la voz."""
    patron, mapa = _cargar()
    if not patron or not texto:
        return texto
    return patron.sub(lambda m: mapa.get(m.group(0)) or mapa.get(m.group(0).lower(), m.group(0)), texto)
