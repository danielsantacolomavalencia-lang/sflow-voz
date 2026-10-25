#!/usr/bin/env python3
"""Enciende / apaga el modo demo de la voz (lo usa la skill /modo-demo) — 30-sep.

Con el modo encendido, el gancho PreToolUse (claude_hooks/voz.py pre) dice cada línea
🔊 que Claude escribe antes de un paso y espera a que Dora termine. Apagado, ese
gancho ni siquiera arranca (settings.json lo filtra con `test -e demo.json`).

    python3 herramientas/modo_demo.py on [--horas 3]
    python3 herramientas/modo_demo.py off
    python3 herramientas/modo_demo.py estado

Se apaga solo al vencer (3 h por defecto), por si nadie lo apaga.
"""
import json
import os
import sys
import time

APP = os.path.expanduser("~/Library/Application Support/SFlow")
RUTA = os.path.join(APP, "demo.json")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "estado"
    if cmd == "on":
        horas = float(sys.argv[sys.argv.index("--horas") + 1]) if "--horas" in sys.argv else 3.0
        os.makedirs(APP, exist_ok=True)
        with open(RUTA, "w", encoding="utf-8") as f:
            json.dump({"desde": time.time(), "hasta": time.time() + horas * 3600}, f)
        print(f"Modo demo ENCENDIDO por {horas:g} h (se apaga solo). SFlow abierta: "
              f"{'sí' if os.path.exists(os.path.join(APP, 'voz.json')) else 'no encontré voz.json'}.")
    elif cmd == "off":
        try:
            os.remove(RUTA)
            print("Modo demo APAGADO.")
        except OSError:
            print("El modo demo ya estaba apagado.")
    else:
        try:
            with open(RUTA, encoding="utf-8") as f:
                resta = (json.load(f)["hasta"] - time.time()) / 60
            print(f"Modo demo encendido; se apaga solo en {resta:.0f} min." if resta > 0
                  else "Modo demo vencido (el gancho lo borra en su siguiente paso).")
        except (OSError, ValueError, KeyError):
            print("Modo demo apagado.")


if __name__ == "__main__":
    main()
