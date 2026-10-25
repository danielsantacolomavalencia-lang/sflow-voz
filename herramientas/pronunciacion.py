#!/usr/bin/env python3
"""Administra el diccionario de pronunciación de la voz de lectura (core/pronunciacion.py).

El ciclo ("Flow, suma esta palabra"):
  1. probar  → SFlow dice la frase como suena hoy y con cada escritura candidata,
               numeradas ("Opción uno…"), con la voz y velocidad reales.
  2. Eliges la que suena bien.
  3. sumar   → queda en el diccionario de la app instalada Y en la copia del repo
               (commitear esa copia). Efecto inmediato: SFlow relee el archivo.

    python3 herramientas/pronunciacion.py lista
    python3 herramientas/pronunciacion.py probar Groq grok gróc --frase "Uso Groq para transcribir."
    python3 herramientas/pronunciacion.py sumar Groq grok
    python3 herramientas/pronunciacion.py quitar Groq
    python3 herramientas/pronunciacion.py decir "Cualquier texto, ya con el diccionario."

`probar` y `decir` hablan por la puerta /voz de SFlow (misma llave que el gancho
de Claude): SFlow tiene que estar abierta y con "Leer en voz los resúmenes de
Claude" marcado. Python 3.9 del sistema, solo biblioteca estándar.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

APP = os.path.expanduser("~/Library/Application Support/SFlow")
RUTAS = [os.path.join(APP, "pronunciacion.json"),                                    # la app instalada
         os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),   # copia del repo
                      "pronunciacion.json")]
_NUMEROS = ["uno", "dos", "tres", "cuatro", "cinco", "seis"]


def _leer():
    """El diccionario (el de la app manda; si no existe, el del repo)."""
    for ruta in RUTAS:
        try:
            with open(ruta, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def _guardar(datos):
    ordenado = dict(sorted(datos.items(), key=lambda kv: (kv[0].startswith("_") is False, kv[0].lower())))
    for ruta in RUTAS:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(ordenado, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, ruta)   # atómico: SFlow nunca lee un archivo a medias


def _decir(texto):
    with open(os.path.join(APP, "voz.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(cfg['puerto'])}/voz",
        data=json.dumps({"texto": texto}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-SFlow-Token": cfg["token"]},
        method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status


def _en_frase(frase, palabra, escritura):
    return re.sub(r"(?<!\w)" + re.escape(palabra) + r"(?!\w)", escritura, frase, flags=re.I)


def main():
    ap = argparse.ArgumentParser(description="Diccionario de pronunciación de Dora")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lista")
    p = sub.add_parser("probar")
    p.add_argument("palabra")
    p.add_argument("opciones", nargs="+")
    p.add_argument("--frase", default=None, help="frase de contexto que contenga la palabra")
    p = sub.add_parser("sumar")
    p.add_argument("palabra")
    p.add_argument("escritura")
    p = sub.add_parser("quitar")
    p.add_argument("palabra")
    p = sub.add_parser("decir")
    p.add_argument("texto")
    a = ap.parse_args()

    datos = _leer()
    if a.cmd == "lista":
        reales = {k: v for k, v in datos.items() if not k.startswith("_")}
        print(f"{len(reales)} palabras en el diccionario:")
        for k, v in reales.items():
            print(f"  {k:<20} → {v}")
    elif a.cmd == "probar":
        frase = a.frase or f"{a.palabra}."
        if not re.search(r"(?<!\w)" + re.escape(a.palabra) + r"(?!\w)", frase, flags=re.I):
            sys.exit(f"La frase no contiene «{a.palabra}».")
        partes = [f"Así suena hoy: {frase}"]
        for i, op in enumerate(a.opciones[:len(_NUMEROS)]):
            partes.append(f"Opción {_NUMEROS[i]}: {_en_frase(frase, a.palabra, op)}")
        texto = " … ".join(partes)
        print(f"SFlow {_decir(texto)} · {texto}")
    elif a.cmd == "sumar":
        anterior = datos.get(a.palabra)
        datos = {k: v for k, v in datos.items() if k.lower() != a.palabra.lower()}
        datos[a.palabra] = a.escritura
        _guardar(datos)
        print(f"«{a.palabra}» → «{a.escritura}»" + (f" (antes: «{anterior}»)" if anterior else " (nueva)"))
    elif a.cmd == "quitar":
        quedan = {k: v for k, v in datos.items() if k.lower() != a.palabra.lower()}
        if len(quedan) == len(datos):
            sys.exit(f"«{a.palabra}» no está en el diccionario.")
        _guardar(quedan)
        print(f"«{a.palabra}» fuera del diccionario.")
    elif a.cmd == "decir":
        print(f"SFlow {_decir(a.texto)}")


if __name__ == "__main__":
    main()
