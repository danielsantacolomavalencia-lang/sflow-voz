"""Preparador de lectura: convierte el formato en algo que se oye bien (7-oct).

El diccionario (core/pronunciacion.py) cambia PALABRAS; esto cambia FORMATO. Corre
justo antes de mandarle el texto a la voz (`Speaker.speak`), en este orden:

  1. estructura — cada renglón es una frase con su pausa:
       · viñetas (-, *, •, ✓, →, [ ]…) se quitan;
       · listas con número o letra (1. 2) a) A. (b)) se dicen según LECTURA_LISTAS:
         "ordinal" (Primero… / Apartado A…), "numero" (Uno… / A…) o "pausa" (nada);
       · títulos (## …) quedan como frase; tablas → celdas separadas por comas;
       · bloques de código, enlaces, negritas y emojis fuera; flechas = pausa.
  2. el diccionario de pronunciación ("Groq" → "grok").
  3. MAYÚSCULAS: si "NUNCA" es palabra del español (lo dice el corrector ortográfico
     de macOS) se lee "nunca"; si es sigla (CRM, IA) se deja para que la deletree.
     Las siglas que el corrector cree palabras (PDF, URL…) van en LECTURA_SIGLAS.

Cuando algo suene raro, APP_DATA_DIR/ultima_lectura.txt tiene el texto original y
el que recibió Dora (lo escribe core/tts.py).
"""
import re

from config import LECTURA_LISTAS, LECTURA_SIGLAS
from core.pronunciacion import aplicar as aplicar_pronunciacion

_ORDINALES = ["Primero", "Segundo", "Tercero", "Cuarto", "Quinto",
              "Sexto", "Séptimo", "Octavo", "Noveno", "Décimo"]
_NUMEROS = ["Uno", "Dos", "Tres", "Cuatro", "Cinco", "Seis", "Siete", "Ocho", "Nueve", "Diez",
            "Once", "Doce", "Trece", "Catorce", "Quince", "Dieciséis", "Diecisiete",
            "Dieciocho", "Diecinueve", "Veinte"]

_VINETA = re.compile(r"^(?:[-*+•·▪◦‣–—✓✔✅☑→➜►▶]+|\[[ xX]\])\s+")
_MARCA = re.compile(r"^(?:\((\d{1,2}|[a-zA-Z])\)|(\d{1,2}|[a-zA-Z])[.)])\s+")
_TITULO = re.compile(r"^#{1,6}\s+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
_MAYUS = re.compile(r"(?<![\w])[A-ZÁÉÍÓÚÜÑ]{2,}(?![\w])")
_FIN = re.compile(r"[.!?…:;,]$")


def _marca_hablada(marca: str, estilo: str) -> str:
    """Cómo se dice el número o la letra de una lista."""
    if estilo == "pausa":
        return ""
    if marca.isdigit():
        n = int(marca)
        if estilo == "ordinal" and 1 <= n <= len(_ORDINALES):
            return _ORDINALES[n - 1] + ": "
        return (_NUMEROS[n - 1] if 1 <= n <= len(_NUMEROS) else marca) + ": "
    letra = marca.upper()
    return (f"Apartado {letra}: " if estilo == "ordinal" else f"{letra}: ")


def _renglon(linea: str, estilo: str) -> str:
    l = linea.strip()
    if not l or re.fullmatch(r"\|?[\s:|-]+\|?", l) and "-" in l:
        return ""                                         # vacío o separador de tabla
    if l.startswith("|"):                                 # fila de tabla → celdas con comas
        l = ", ".join(c.strip() for c in l.strip("|").split("|") if c.strip())
    l = _TITULO.sub("", l)
    dicho = ""
    for _ in range(3):                                    # "A. • texto": marca y viñeta juntas
        m = _MARCA.match(l)
        if m:
            dicho += _marca_hablada(m.group(1) or m.group(2), estilo)
            l = l[m.end():]
            continue
        v = _VINETA.match(l)
        if v:
            l = l[v.end():]
            continue
        break
    l = (dicho + l).strip()
    if l and not _FIN.search(l):
        l += "."                                          # cada renglón con su pausa
    return l


def estructura(texto: str, estilo: str | None = None) -> str:
    """Paso 1: del formato (viñetas, listas, títulos, tablas, markdown) a frases."""
    estilo = estilo or LECTURA_LISTAS
    t = re.sub(r"```.*?```", " ", texto or "", flags=re.S)        # bloques de código
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)                # [texto](enlace) → texto
    t = re.sub(r"https?://\S+", "un enlace", t)
    t = re.sub(r"\*\*|__|`", "", t)                               # negritas y código en línea
    t = re.sub(r"(?<!\w)\*(?=\S)([^*\n]+?)\*(?!\w)", r"\1", t)   # *itálica*
    t = re.sub(r"\s*(?:→|->|=>|⟶|➜)\s*", ", ", t)                 # flecha a mitad de frase = pausa
    t = re.sub(r"(?<=\d)\s*[×x]\s*(?=\d)", " por ", t)
    t = t.replace("≈", " aproximadamente ").replace("&", " y ")
    renglones = [_renglon(l, estilo) for l in t.splitlines()]
    t = " ".join(r for r in renglones if r)
    t = _EMOJI.sub("", t)
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)                       # sin espacio antes del signo
    return re.sub(r"\s+", " ", t).strip()


_es_palabra_cache: dict[str, bool] = {}


def _es_palabra(palabra: str) -> bool:
    """¿El corrector ortográfico de macOS (español) la conoce? (se recuerda)."""
    if palabra in _es_palabra_cache:
        return _es_palabra_cache[palabra]
    try:
        import AppKit
        sc = AppKit.NSSpellChecker.sharedSpellChecker()
        rango, _ = sc.checkSpellingOfString_startingAt_language_wrap_inSpellDocumentWithTag_wordCount_(
            palabra, 0, "es", False, 0, None)
        ok = rango.length == 0
    except Exception:
        ok = False   # sin corrector: no se toca (mejor deletrear que inventar)
    _es_palabra_cache[palabra] = ok
    return ok


def mayusculas(texto: str) -> str:
    """Paso 3: "NUNCA" → "nunca" si es palabra del español; las siglas se quedan."""
    def cambiar(m):
        w = m.group(0)
        if w in LECTURA_SIGLAS or not _es_palabra(w.lower()):
            return w
        return w.lower()
    return _MAYUS.sub(cambiar, texto)


def preparar(texto: str, estilo: str | None = None) -> str:
    """El texto listo para la voz: estructura → diccionario → mayúsculas."""
    return mayusculas(aplicar_pronunciacion(estructura(texto, estilo)))
