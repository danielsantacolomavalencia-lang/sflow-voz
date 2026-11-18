# Voz directa: le manda una frase a SFlow y espera a que termine de decirla.
# Uso: /usr/bin/python3 -I ~/.claude/skills/modo-voz/decir.py 'frase'
#
# No depende del gancho de las líneas 🔊 (que lee la respuesta escrita): va directo a la
# puerta local de SFlow. Al esperar el final, la acción siguiente no pisa la voz.
# Si SFlow no responde, lo dice en una línea ("SIN VOZ: …") y sale con código 2, para que
# Claude lo note y avise; la respuesta escrita es siempre el respaldo.
# Cada frase deja una línea "directa ·" en SFlow/voz.log, junto a las del gancho.
# Usa el acceso directo que deja claude_hooks/instalar.py en la carpeta de datos de SFlow,
# así funciona estés donde estés clonado.
import os
import sys
import time

SFLOW_APP = os.path.expanduser("~/Library/Application Support/SFlow")

texto = " ".join(sys.argv[1:]).strip()
if not texto:
    sys.exit(0)

try:
    sys.path.insert(0, SFLOW_APP)
    import voz
    n = voz._enviar(texto)
except Exception as e:  # SFlow cerrada, sin instalar.py o sin configuración
    print(f"SIN VOZ: SFlow no respondió ({type(e).__name__}); la frase no sonó.")
    if "voz" in sys.modules:
        voz._log(f"directa · {len(texto)} chars · SIN VOZ ({type(e).__name__})")
    sys.exit(2)
voz._log(f"directa · {len(texto)} chars → SFlow #{n}")

# Tope de espera según el largo: unas 2,5 palabras por segundo, más margen.
tope = min(120, 15 + 0.5 * len(texto.split()))
fin = time.monotonic() + tope
while n is not None and time.monotonic() < fin:
    try:
        if voz._terminado() >= n:
            break
    except Exception:
        break
    time.sleep(0.25)
else:
    if n is not None:
        voz._log(f"directa · #{n} sin confirmar el final (tope {tope:.0f} s)")
