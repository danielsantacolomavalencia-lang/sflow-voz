#!/usr/bin/env python3
"""Conecta SFlow con Claude Code: skills de modo voz + ganchos que leen las líneas 🔊.

    /usr/bin/python3 claude_hooks/instalar.py            # instala (o actualiza)
    /usr/bin/python3 claude_hooks/instalar.py --quitar   # deja ~/.claude como estaba

Qué hace:
  0. Deja accesos directos estables en ~/Library/Application Support/SFlow/ (voz.py y modo_demo.py apuntan a
     ESTA carpeta). Ahí los buscan las skills y Arbrain («Voz con SFlow»), estés donde estés clonado.
  1. Copia las skills `modo-voz` y `modo-demo` a ~/.claude/skills/.
  2. Agrega a ~/.claude/settings.json dos ganchos (antes guarda una copia `settings.json.antes-de-sflow`):
       · Stop       → claude_hooks/voz.py       (el resumen 🔊 al terminar cada respuesta)
       · PreToolUse → claude_hooks/voz.py pre   (solo con el modo demo encendido: cuesta milisegundos apagado)
Es idempotente: correrlo dos veces no duplica nada. Python 3.9 del sistema, solo biblioteca estándar.
"""
import json
import os
import shutil
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
SFLOW = os.path.dirname(AQUI)
CLAUDE = os.path.expanduser("~/.claude")
APP = os.path.expanduser("~/Library/Application Support/SFlow")
ENLACES = {"voz.py": os.path.join(AQUI, "voz.py"), "modo_demo.py": os.path.join(SFLOW, "herramientas", "modo_demo.py")}
SKILLS = ("modo-voz", "modo-demo")
VOZ = os.path.join(AQUI, "voz.py")
MARCA = "claude_hooks/voz.py"   # así se reconocen los ganchos de SFlow al actualizar o quitar

GANCHOS = {
    "Stop": {"hooks": [{"type": "command", "command": f"/usr/bin/python3 '{VOZ}'"}]},
    "PreToolUse": {"hooks": [{"type": "command", "timeout": 20, "command":
        f"test -e \"$HOME/Library/Application Support/SFlow/demo.json\" && exec /usr/bin/python3 '{VOZ}' pre || true"}]},
}


def _sin_sflow(lista):
    """Los grupos de ganchos que NO son de SFlow (los tuyos quedan intactos)."""
    return [g for g in lista if not any(MARCA in h.get("command", "") for h in g.get("hooks", []))]


def main():
    quitar = "--quitar" in sys.argv
    os.makedirs(os.path.join(CLAUDE, "skills"), exist_ok=True)

    os.makedirs(APP, exist_ok=True)
    for nombre, destino in ENLACES.items():
        enlace = os.path.join(APP, nombre)
        if os.path.islink(enlace):
            os.remove(enlace)
        if not quitar and not os.path.exists(enlace):
            os.symlink(destino, enlace)

    for nombre in SKILLS:
        destino = os.path.join(CLAUDE, "skills", nombre)
        if quitar:
            shutil.rmtree(destino, ignore_errors=True)
            continue
        os.makedirs(destino, exist_ok=True)
        with open(os.path.join(AQUI, "skills", nombre, "SKILL.md"), encoding="utf-8") as f:
            texto = f.read()
        with open(os.path.join(destino, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(texto)

    ruta = os.path.join(CLAUDE, "settings.json")
    ajustes = {}
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            ajustes = json.load(f)
        respaldo = ruta + ".antes-de-sflow"
        if not os.path.exists(respaldo):
            shutil.copy2(ruta, respaldo)
    hooks = ajustes.setdefault("hooks", {})
    for evento, grupo in GANCHOS.items():
        lista = _sin_sflow(hooks.get(evento, []))
        if not quitar:
            lista.append(grupo)
        if lista:
            hooks[evento] = lista
        else:
            hooks.pop(evento, None)
    if not hooks:
        ajustes.pop("hooks", None)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(ajustes, f, ensure_ascii=False, indent=2)
        f.write("\n")

    if quitar:
        print("Listo: quité las skills de modo voz y los ganchos de SFlow de ~/.claude.")
    else:
        print(f"Listo: accesos directos en {APP} (apuntan a {SFLOW}), skills modo-voz y modo-demo")
        print("en ~/.claude/skills y ganchos de voz en ~/.claude/settings.json.")
        print("Abre un chat nuevo de Claude Code (o de Arbrain) y di «modo voz».")


if __name__ == "__main__":
    main()
