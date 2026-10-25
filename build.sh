#!/bin/bash
# build.sh — Construye (y opcionalmente instala) SFlow.app
#
#   bash build.sh              → construye en dist/
#   bash build.sh --install    → construye, reemplaza /Applications/SFlow.app y la lanza
#
# CANDADO DE FIRMA: la firma ad-hoc (`--sign -`) cambia la
# huella del binario en cada rebuild y macOS revoca Accesibilidad / Micrófono /
# Monitorización de entrada SIN AVISAR: la app se ve viva y queda sorda. Se firma
# SIEMPRE con el certificado local estable "SFlow Dev" (Acceso a Llaveros →
# Asistente de Certificados → Crear un certificado… → nombre "SFlow Dev", raíz
# autofirmada, tipo "Firma de código"). Si no existe, el build aborta.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

IDENTITY="${SIGN_ID:-SFlow Dev}"
APP_DEST="/Applications/SFlow.app"

echo "=== SFlow build ==="

# --- 1) Icono ---
echo "[1/6] Icono"
if [ ! -f "SFlow.icns" ]; then
    ICONSET="SFlow.iconset"; mkdir -p "$ICONSET"
    for size in 16 32 64 128 256 512; do
        sips -z $size $size logo.png --out "$ICONSET/icon_${size}x${size}.png" > /dev/null 2>&1
        double=$((size * 2))
        sips -z $double $double logo.png --out "$ICONSET/icon_${size}x${size}@2x.png" > /dev/null 2>&1
    done
    iconutil -c icns "$ICONSET" -o SFlow.icns; rm -rf "$ICONSET"
    echo "   SFlow.icns creado."
else
    echo "   SFlow.icns ya existe."
fi

# --- 2) Certificado: se comprueba ANTES de gastar 2 minutos en el build ---
echo "[2/6] Certificado de firma"
if ! security find-identity -v -p codesigning | grep -q "\"$IDENTITY\""; then
    echo "   ERROR: no existe el certificado '$IDENTITY'. Créalo en Acceso a Llaveros"
    echo "   (Asistente de Certificados → Crear un certificado… → tipo 'Firma de código')."
    exit 1
fi
echo "   '$IDENTITY' encontrado."

# --- 3) Venv + PyInstaller ---
echo "[3/6] Venv + PyInstaller"
source venv/bin/activate
pip install --quiet pyinstaller

# --- 4) Build ---
echo "[4/6] Construyendo .app (~1-2 min)"
rm -rf build/ dist/
pyinstaller sflow.spec --noconfirm 2>&1 | tail -3
[ -d dist/SFlow.app ] || { echo "   ERROR: PyInstaller no produjo dist/SFlow.app"; exit 1; }

# --- 5) Firma ---
echo "[5/6] Firmando con '$IDENTITY'"
# Con la sesión BLOQUEADA codesign se cuelga esperando el llavero: tope de 120 s.
if ! perl -e 'alarm 120; exec @ARGV' codesign --force --deep --sign "$IDENTITY" dist/SFlow.app; then
    echo "   ERROR: no se pudo firmar. Si tardó 120 s: la Mac está bloqueada, desbloquea y reintenta."
    exit 1
fi
codesign --verify --deep --strict dist/SFlow.app && echo "   Firma verificada (TCC estable entre rebuilds)."

# --- 6) Instalar ---
if [ "${1:-}" == "--install" ]; then
    echo "[6/6] Instalando en $APP_DEST"
    # Candado: SFlow v3 (sflow-next, en Swift) se instala en la MISMA ruta. Esta versión (2.x) no la pisa
    # salvo que lo pidas con --forzar (la anterior iría a la Papelera, como siempre).
    VER_ACTUAL=$(defaults read "$APP_DEST/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo "")
    if [ -n "$VER_ACTUAL" ] && [ "${VER_ACTUAL%%.*}" -ge 3 ] 2>/dev/null && [ "${2:-}" != "--forzar" ]; then
        echo "   ALTO: ya tienes SFlow $VER_ACTUAL (v3) en $APP_DEST y esta versión es la 2.x en Python."
        echo "   No la reemplazo. Usa dist/SFlow.app desde aquí, o: bash build.sh --install --forzar"
        exit 1
    fi
    pkill -x SFlow 2>/dev/null || true
    sleep 1
    # La versión anterior va a la Papelera, nunca rm -rf: un build malo debe poder deshacerse.
    if [ -d "$APP_DEST" ]; then
        mv "$APP_DEST" "$HOME/.Trash/SFlow-$(date +%Y%m%d-%H%M%S).app"
    fi
    ditto dist/SFlow.app "$APP_DEST"          # ditto, NO cp -r (cp corrompe el bundle)
    xattr -cr "$APP_DEST" 2>/dev/null || true # sin cuarentena de Gatekeeper
    # LaunchServices cachea el icono por ruta: refrescar para que no quede el genérico.
    touch "$APP_DEST"
    LSREG=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister
    [ -x "$LSREG" ] && "$LSREG" -f "$APP_DEST" || true
    echo "   Instalada. Lanzando..."
    open -n "$APP_DEST"
    sleep 3
    if pgrep -x SFlow >/dev/null; then
        echo "   SFlow corriendo desde $APP_DEST."
    else
        echo "   AVISO: no arrancó. Revisa Consola.app filtrando por SFlow."
    fi
else
    echo "[6/6] Sin instalar (usa --install para reemplazar $APP_DEST)."
fi

echo ""
echo "=== BUILD COMPLETO ==="
echo "  Bundle: $(pwd)/dist/SFlow.app  ($(du -sh dist/SFlow.app | cut -f1))"
