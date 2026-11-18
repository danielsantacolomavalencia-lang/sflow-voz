#!/usr/bin/env python3
"""Ganchos de Claude Code → SFlow dice en voz las líneas 🔊 (resumen y modo demo).

Dos momentos:

  · `Stop` (al terminar la respuesta) — el resumen en voz. La persona pide "resumen en voz"
    / "modo voz"; Claude cierra con un bloque que empieza con 🔊 (skill modo-voz). El
    script se desprende a segundo plano: Claude no espera.

  · `PreToolUse` (antes de cada herramienta), SOLO con el modo demo encendido
    (skill /modo-demo → herramientas/modo_demo.py crea APP/demo.json; se apaga solo a
    las 3 h). Durante una demo en pantalla, Claude escribe una línea 🔊 corta antes de
    cada paso visible; este gancho la manda a SFlow y ESPERA a que la voz termine (tope
    ESPERA_MAX) antes de dejar correr la acción: lo que la persona oye y lo que ve van
    juntos. Nunca inyecta nada en la conversación: Claude trabaja igual.
    En settings.json va detrás de un `test -e demo.json`: apagado cuesta milisegundos.

Reglas de lectura:
  · Solo cuenta un 🔊 que abre línea (uno citado a mitad de texto no). En modo demo
    cada narración termina al final de SU LÍNEA (la línea técnica que venga debajo no
    se oye); el resumen termina al final de su párrafo (la firma no se oye).
  · Solo se mira la respuesta en curso (después del último mensaje de la persona) y cada
    bloque se dice UNA vez (APP/voz_estado/<sesión>.json).
  · Fuera del modo demo, Stop dice solo el último bloque (el resumen), como siempre.
  · Sin 🔊 pero la persona pidió voz ("modo voz", "léeme", "en voz"...) y la respuesta es
    corta: se lee completa (chats abiertos antes de la regla no saben poner la marca).
    Salvo que esa respuesta ya haya hablado por la voz directa (skill modo-voz →
    decir.py): ya dijo su resumen, leer la respuesta sería oírla dos veces.

Diagnóstico sin hablar: `python3 voz.py --probar <transcript.jsonl> [--demo]`.
Python 3.9 del sistema (/usr/bin/python3), solo biblioteca estándar. Una línea por
envío en APP/voz.log. Si SFlow no está abierta, no pasa nada y nadie espera.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

APP = os.path.expanduser("~/Library/Application Support/SFlow")
MARCA = "\U0001F50A"   # 🔊
_LEER_COLA = 4 * 1024 * 1024   # basta con el final del transcript (pueden ser decenas de MB)
_MAX_SIN_MARCA = 1000          # sin 🔊 solo se lee una respuesta corta (≈ 1 min de voz)
_PIDIO_VOZ = re.compile(r"(modo voz|en voz|l[eé]eme|resumen hablado|mant[eé]n la voz)", re.I)
ESPERA_MAX = 10.0              # modo demo: lo más que una acción espera a la voz
_ESTADO_DIR = os.path.join(APP, "voz_estado")
_DEMO = os.path.join(APP, "demo.json")


def _log(msg):
    try:
        ruta = os.path.join(APP, "voz.log")
        if os.path.exists(ruta) and os.path.getsize(ruta) > 200_000:
            os.replace(ruta, ruta + ".1")
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def demo_activo():
    """¿Está encendido el modo demo? (se apaga solo al vencer)."""
    try:
        with open(_DEMO, encoding="utf-8") as f:
            hasta = float(json.load(f).get("hasta", 0))
    except (OSError, ValueError, AttributeError):
        return False
    if time.time() > hasta:
        try:
            os.remove(_DEMO)
        except OSError:
            pass
        return False
    return True


# ── leer el transcript ────────────────────────────────────────────────────
def _entradas(transcript):
    """Las entradas del final del transcript (JSONL), en orden."""
    with open(transcript, "rb") as f:
        f.seek(0, os.SEEK_END)
        tam = f.tell()
        f.seek(max(0, tam - _LEER_COLA))
        crudo = f.read().decode("utf-8", errors="ignore")
    lineas = crudo.splitlines()
    if tam > _LEER_COLA:
        lineas = lineas[1:]   # la primera puede venir cortada
    out = []
    for l in lineas:
        try:
            out.append(json.loads(l))
        except ValueError:
            continue
    return out


def _es_pregunta_de_la_persona(e):
    """Una entrada 'user' que es un mensaje real (no un resultado de herramienta)."""
    if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
        return False
    c = (e.get("message") or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip())
    if isinstance(c, list):
        tipos = {b.get("type") for b in c if isinstance(b, dict)}
        return "text" in tipos and "tool_result" not in tipos
    return False


def _texto_de(e):
    c = (e.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text") or "" for b in c or [] if isinstance(b, dict) and b.get("type") == "text")


def _turno(entradas):
    """(pregunta de la persona, [(uuid, texto, solo_texto)] de la respuesta en curso).

    Solo mira después de la última pregunta: un 🔊 de una respuesta anterior nunca
    se vuelve a decir. `solo_texto` = esa entrada no llama herramientas (la del final)."""
    bloques = []
    for e in reversed(entradas):
        if _es_pregunta_de_la_persona(e):
            return _texto_de(e), bloques[::-1]
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        cont = (e.get("message") or {}).get("content") or []
        solo_texto = all(isinstance(b, dict) and b.get("type") == "text" for b in cont)
        for b in reversed(cont):
            if isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip():
                bloques.append((e.get("uuid"), b["text"], solo_texto))
    return "", bloques[::-1]


def _llamo_la_herramienta(entradas, tool_use_id, tool_name, tool_input):
    """¿Ya llegó al archivo el mensaje que pide esta herramienta? (con su texto 🔊)."""
    vistos = 0
    for e in reversed(entradas):
        if e.get("type") != "assistant":
            continue
        for b in (e.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                if tool_use_id and b.get("id") == tool_use_id:
                    return True
                if not tool_use_id and b.get("name") == tool_name and b.get("input") == tool_input:
                    return True
        vistos += 1
        if vistos > 6:
            return False
    return False


# ── los bloques 🔊 ─────────────────────────────────────────────────────────
def _inicios(texto):
    """Posiciones de cada 🔊 que abre una línea (los citados a mitad de línea no cuentan)."""
    pos, i = [], 0
    for linea in texto.splitlines(keepends=True):
        if linea.lstrip(" \t>*-_").startswith(MARCA):
            pos.append(i + linea.index(MARCA))
        i += len(linea)
    return pos


def _inicio_del_resumen(texto):
    """El ÚLTIMO 🔊 que abre una línea, o None."""
    p = _inicios(texto)
    return p[-1] if p else None


def _bloques(texto, hasta="parrafo"):
    """[(posición, bloque)] — de cada 🔊 que abre línea hasta el fin de su línea
    (hasta="linea", modo demo) o de su párrafo (resumen), sin pasar del siguiente 🔊."""
    p = _inicios(texto)
    corte = "\n" if hasta == "linea" else "\n\n"
    out = []
    for a, b in zip(p, p[1:] + [len(texto)]):
        fin = texto.find(corte, a)
        out.append((a, texto[a:min(b, fin) if fin != -1 else b]))
    return out


def _limpiar_texto(texto):
    texto = re.sub(r"```.*?```", " ", texto, flags=re.S)       # bloques de código
    lineas = [l.strip() for l in texto.strip().splitlines() if not l.strip().startswith("|")]
    while lineas and (not lineas[-1] or re.match(r"^[—–-]\s*\S{1,20}$", lineas[-1])):
        lineas.pop()   # la firma ("— Flow") y líneas vacías del final
    t = "\n".join(l for l in lineas if l)   # renglones: SFlow los vuelve frases (core/lectura.py)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)          # [texto](enlace) → texto
    t = re.sub(r"\*\*|__|[*`#>]", "", t)                      # negritas, código, títulos (no el _ de un nombre)
    t = re.sub(r"^\s*(resumen( en voz)?)\s*[:·—-]\s*", "", t, flags=re.I)
    return "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in t.splitlines() if l.strip())


def limpiar(texto, desde_marca=True):
    """Del último 🔊 que abre línea al final (o todo, si no hay marca): listo para oírse."""
    if desde_marca:
        texto = texto[_inicio_del_resumen(texto) + len(MARCA):]
    return _limpiar_texto(texto)


def _hablo_directo(entradas):
    """¿La respuesta en curso ya habló por la voz directa (skill modo-voz, decir.py)?"""
    for e in reversed(entradas):
        if _es_pregunta_de_la_persona(e):
            return False
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        for b in (e.get("message") or {}).get("content") or []:
            if (isinstance(b, dict) and b.get("type") == "tool_use"
                    and "modo-voz/decir.py" in str((b.get("input") or {}).get("command", ""))):
                return True
    return False


def que_decir(entradas, demo=False, dichos=frozenset(), respaldo=True):
    """[(clave, texto listo para oírse)] pendientes de decir, en orden.

    demo=True: todos los bloques 🔊 de la respuesta en curso que aún no se dijeron.
    demo=False: solo el último (el resumen), como en la fase 1."""
    pregunta, textos = _turno(entradas)
    hasta = "linea" if demo else "parrafo"
    todos = [(f"{uuid}:{pos}", bloque) for uuid, texto, _ in textos for pos, bloque in _bloques(texto, hasta)]
    if todos:
        elegidos = todos if demo else todos[-1:]
        return [(k, _limpiar_texto(b[len(MARCA):])) for k, b in elegidos
                if k not in dichos and _limpiar_texto(b[len(MARCA):])]
    # Sin 🔊: solo si pidió voz, la respuesta ya terminó, es corta y no habló directo.
    if respaldo and textos and _PIDIO_VOZ.search(pregunta) and not _hablo_directo(entradas):
        uuid, texto, solo_texto = textos[-1]
        clave = f"{uuid}:todo"
        if solo_texto and len(texto) <= _MAX_SIN_MARCA and clave not in dichos:
            return [(clave, limpiar(texto, desde_marca=False))]
    return []


# ── memoria de lo ya dicho, por sesión ────────────────────────────────────
def _ruta_estado(sesion):
    return os.path.join(_ESTADO_DIR, re.sub(r"[^\w-]", "", sesion or "sin-sesion") + ".json")


def _dichos(sesion):
    try:
        with open(_ruta_estado(sesion), encoding="utf-8") as f:
            return set(json.load(f).get("dichos", []))
    except (OSError, ValueError, AttributeError):
        return set()


def _guardar_dichos(sesion, dichos):
    try:
        os.makedirs(_ESTADO_DIR, exist_ok=True)
        tmp = _ruta_estado(sesion) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"dichos": sorted(dichos)[-400:]}, f)
        os.replace(tmp, _ruta_estado(sesion))
        for n in os.listdir(_ESTADO_DIR):   # limpieza: sesiones de hace más de 3 días
            r = os.path.join(_ESTADO_DIR, n)
            if time.time() - os.path.getmtime(r) > 3 * 86400:
                os.remove(r)
    except OSError:
        pass


# ── hablar con SFlow ──────────────────────────────────────────────────────
def _cfg():
    with open(os.path.join(APP, "voz.json"), encoding="utf-8") as f:
        return json.load(f)


def _voz_de_quien_habla():
    """Voz propia para Claude Code (opcional), para distinguirla de otros agentes que también hablan con SFlow.

    `voz_claude_code.json` en la carpeta de SFlow, con {"voz": "noel"} (o "dora" / "alex"), la enciende. Un agente que
    corre dentro de Arbrain (trae ARBRAIN_HOME en su entorno) sigue con la voz del menú. Un SFlow anterior ignora el campo.
    """
    if os.environ.get("ARBRAIN_HOME"):
        return {}
    try:
        with open(os.path.join(APP, "voz_claude_code.json"), encoding="utf-8") as f:
            v = str(json.load(f).get("voz", "")).strip().lower()
        return {"voz": v} if v in ("dora", "alex", "noel") else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _enviar(texto):
    """Manda una frase a SFlow. Devuelve su número (o None si SFlow es anterior)."""
    cfg = _cfg()
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(cfg['puerto'])}/voz",
        data=json.dumps({"texto": texto, **_voz_de_quien_habla()}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-SFlow-Token": cfg["token"]},
        method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read() or b"{}").get("id")


def _terminado():
    cfg = _cfg()
    req = urllib.request.Request(f"http://127.0.0.1:{int(cfg['puerto'])}/voz/estado",
                                 headers={"X-SFlow-Token": cfg["token"]})
    with urllib.request.urlopen(req, timeout=2) as r:
        return int(json.loads(r.read()).get("terminado", 0))


def _decir(pendientes, sesion, dichos):
    """Manda las frases en orden; devuelve el número de la última (o None)."""
    ultimo = None
    for clave, texto in pendientes:
        ultimo = _enviar(texto)
        dichos.add(clave)
        _log(f"{sesion[:8]} · {len(texto)} chars → SFlow #{ultimo}")
    _guardar_dichos(sesion, dichos)
    return ultimo


# ── los dos momentos ──────────────────────────────────────────────────────
def _al_terminar(entrada):
    """Stop, en segundo plano: el resumen (o, en modo demo, lo que falte decir)."""
    transcript = entrada.get("transcript_path") or ""
    sesion = entrada.get("session_id") or ""
    demo = demo_activo()
    dichos = _dichos(sesion)
    pendientes = []
    for _ in range(8):   # el final de la respuesta puede tardar un instante en llegar al archivo
        try:
            pendientes = que_decir(_entradas(transcript), demo=demo, dichos=dichos)
        except OSError as e:
            _log(f"{sesion[:8]} · no pude leer el transcript: {e}")
            return
        if pendientes:
            break
        time.sleep(0.35)
    if not pendientes:
        return
    try:
        _decir(pendientes, sesion, dichos)
    except Exception as e:
        _log(f"{sesion[:8]} · no llegó a SFlow: {e}")


def _antes_de_la_accion(entrada):
    """PreToolUse (modo demo): dice la línea 🔊 nueva y espera a que Dora termine."""
    if not demo_activo():
        return
    transcript = entrada.get("transcript_path") or ""
    sesion = entrada.get("session_id") or ""
    tid, nombre, entrada_tool = entrada.get("tool_use_id"), entrada.get("tool_name"), entrada.get("tool_input")
    t0 = time.monotonic()
    entradas = []
    while time.monotonic() - t0 < 1.5:   # que el mensaje con su 🔊 ya esté en el archivo
        try:
            entradas = _entradas(transcript)
        except OSError:
            return
        if _llamo_la_herramienta(entradas, tid, nombre, entrada_tool):
            break
        time.sleep(0.1)
    dichos = _dichos(sesion)
    pendientes = que_decir(entradas, demo=True, dichos=dichos, respaldo=False)
    if not pendientes:
        return
    try:
        ultimo = _decir(pendientes, sesion, dichos)
    except Exception as e:
        _log(f"{sesion[:8]} · no llegó a SFlow: {e}")
        return
    if ultimo is None:
        return
    fin = time.monotonic() + ESPERA_MAX
    while time.monotonic() < fin:   # sincronizado: la acción espera a la voz (con tope)
        try:
            if _terminado() >= ultimo:
                break
        except Exception:
            break
        time.sleep(0.2)
    _log(f"{sesion[:8]} · {nombre} esperó {time.monotonic() - t0:.1f} s a la voz")


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--probar":
        demo = "--demo" in sys.argv
        for clave, texto in que_decir(_entradas(sys.argv[2]), demo=demo):
            print(f"diría ({len(texto)} chars): {texto}")
        return
    if len(sys.argv) > 2 and sys.argv[1] == "--hijo":
        _al_terminar(json.loads(sys.argv[2]))
        return
    try:
        entrada = json.load(sys.stdin)
    except ValueError:
        return
    if len(sys.argv) > 1 and sys.argv[1] == "pre":
        _antes_de_la_accion(entrada)   # síncrono: esa es la idea
        return
    # Stop: a segundo plano y fuera, Claude Code no espera por la voz.
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--hijo", json.dumps(entrada)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:   # un gancho nunca debe romperle nada a Claude Code
        _log(f"error: {e}")
    sys.exit(0)
