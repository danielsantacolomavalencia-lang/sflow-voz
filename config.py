import os
# Las barras de progreso de Hugging Face usan tqdm, y tqdm crea un candado de
# multiprocessing que en macOS arranca un ayudante con `sys.executable -c ...`.
# Dentro del .app ese ejecutable ES SFlow: sin freeze_support() eso fue una
# bomba de procesos (1-sep). Se apaga el gatillo aquí, antes de cualquier import.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
import sys
from dotenv import load_dotenv


def _get_resource_dir() -> str:
    """Read-only bundled assets (logo, etc). PyInstaller puts them in sys._MEIPASS."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _get_data_dir() -> str:
    """Writable user data (DB, .env). In bundle → ~/Library/Application Support/SFlow/."""
    if getattr(sys, "frozen", False):
        return os.path.expanduser("~/Library/Application Support/SFlow")
    return os.path.dirname(os.path.abspath(__file__))


_RESOURCE_DIR = _get_resource_dir()
_DATA_DIR = _get_data_dir()

# Ensure data directory exists when running as bundle
if getattr(sys, "frozen", False):
    os.makedirs(_DATA_DIR, exist_ok=True)

# Load .env from data dir
load_dotenv(os.path.join(_DATA_DIR, ".env"))

# Groq API
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "whisper-large-v3-turbo"

# --- Motor de transcripción ---
# "local": mlx-whisper en el Apple Silicon (mismo modelo que Groq, corriendo en
#          la Mac vía MLX; ~950 ms vs ~500 ms de Groq, misma precisión, $0 y sin
#          internet).
# "groq":  la nube. Siempre es el fallback: mientras el modelo local carga (o
#          se descarga la primera vez, ~1.6 GB), y si el local falla.
STT_ENGINE_DEFAULT = "local"
LOCAL_MODEL_ID = "mlx-community/whisper-large-v3-turbo"
# MLX guarda en caché los buffers de cada dictado y no los suelta: el 26-sep
# SFlow pasó de 2.2 GB a 4.6 GB en dos horas. Con este tope, y limpiando tras
# cada dictado, la memoria se queda fija. Medido: misma velocidad.
LOCAL_MLX_CACHE_MB = 256
# Red de seguridad: si el local tarda más que esto para un audio de D segundos,
# ese dictado se manda también a Groq y gana el primero que termine.
# Lo normal medido en un M4: ~2 s hasta 25 s de audio, ~4.3 s con 49 s.
STT_LOCAL_LIMITE_BASE_S = 4.0
STT_LOCAL_LIMITE_POR_S = 0.15
STT_TOPE_TOTAL_S = 45.0   # pase lo que pase, el orbe no se queda "pensando" más que esto + D
STT_ENGINES = [("Local · Whisper en tu Mac (sin internet)", "local"), ("Groq · nube", "groq")]

# Frases que Whisper alucina sobre silencio o ruido (créditos de YouTube y
# similares). Si la transcripción las contiene, no es voz: se descarta.
# Traído del repo de Carreón (transcriber_groq.py).
STT_HALLUCINATIONS = (
    "gracias por ver el video", "gracias por ver este video", "gracias por ver.",
    "subtitulado por la comunidad", "subtítulos por la comunidad",
    "subtitulos realizados por la comunidad", "subtítulos realizados por la comunidad",
    "amara.org", "suscríbete al canal", "suscribete al canal",
    "thank you for watching", "thanks for watching", "please subscribe", "see you next time",
)
WHISPER_LANGUAGE = "es"  # Explicit language for accurate accents (é, ó, ñ, etc.)

# Lexical bias for Whisper: proper nouns it cannot guess (the name of your assistant, your
# tools...). Keep it SHORT: on silent/noisy audio Whisper can echo the prompt back.
STT_PROMPT = "También uso SFlow, Claude y Groq."

# Guaranteed fixes applied after transcription (regex, replacement).
# The prompt lowers the error rate; these make it deterministic. Example, for an
# assistant called "Bruno" that Whisper writes "Bruño":
#     (r"\b[Bb]ru[nñ]o\b", "Bruno"),
# Word-bounded, so it never touches other words.
STT_CORRECTIONS = []

# Audio
SAMPLE_RATE = 16000
CHANNELS = 1
AUDIO_DTYPE = "int16"
BLOCK_SIZE = 1024

# Orb (ui/orb.py: el orbe de dos capas)
# 30 fps: el movimiento del orbe es lento (respiración, convección, titileo) y
# a 30 se ve igual que a 60, por la mitad de CPU.
ORB_FPS = 30
ORB_IDLE_FREEZE_S = 10.0  # tras esto sin actividad: "reposo vivo" (lento) o congelado si está apagado
# Reposo vivo: en vez de congelarse, el orbe sigue moviéndose lento. MEDIDO en la app instalada
# (pill 110, 12 cps): ~14.5 % de UN núcleo (10.5 % hilo principal + ~4 % hilos de pintura de Qt).
# Menú "Movimiento en reposo"; con batería se congela.
ORB_REPOSO_FPS = 12
ORB_REPOSO_LENTO = 0.5    # velocidad del movimiento en reposo vivo (1 = normal)
ORB_PALETTE_FADE_S = 0.35  # crossfade between state palettes

# UI — el pill es cuadrado: el color del orbe comunica el estado.
# El tamaño se elige desde el menú de la barra y se guarda en settings.json.
PILL_SIZE_PRESETS = [("Pequeño (63 px)", 63), ("Mediano (78 px)", 78), ("Grande (95 px)", 95),
                     ("Extra grande (110 px)", 110), ("Gigante (130 px)", 130)]
PILL_SIZE_DEFAULT = 95
# El lienzo del orbe es el 92% del pill: el resto es halo + aro del fondo, por
# eso el orbe FLOTA. A 100% el halo pisa el borde del pill.
ORB_FILL = 0.92
ORB_HALO_INT = 1.15  # intensidad del halo blanco del orbe
PILL_HEIGHT = PILL_SIZE_DEFAULT   # (arranque; el pill lo relee de settings)
# (legacy) widths from when the pill stretched to show audio bars. Kept in case
# that look is ever wanted back; nothing reads them today.
PILL_WIDTH_IDLE = 63
PILL_WIDTH_RECORDING = 183
PILL_WIDTH_STATUS = 94
# Background opacity is RADIAL: solid behind the orb so it always has contrast
# (a translucent background washes out over light windows), fading to the glassy edge:
# solid to 70% of the radius, 0.70 at the rim, clipped to a circle (no pen: no line).
PILL_OPACITY_CORE = 1.00  # disc right behind the orb
PILL_OPACITY = 0.70       # at the pill's rim
PILL_CORE_STOP = 0.70     # where the solid disc ends (fraction of the radius)
PILL_BG_RGB = (18, 18, 22)  # gris casi negro: cualquier paleta del orbe flota sobre él
PILL_CORNER_RADIUS = PILL_HEIGHT // 2   # círculo perfecto
PILL_MARGIN_BOTTOM = 14
LOGO_SIZE = int(PILL_HEIGHT * ORB_FILL)   # el orbe dentro del pill

# Logo path (read-only bundled asset) — menu bar + app icon
LOGO_PATH = os.path.join(_RESOURCE_DIR, "logo_small.png")

# Audio Visualizer
NUM_BARS = 20
VIZ_FPS = 60
BAR_DECAY = 0.85
BAR_GAIN = 8.0

# Hotkey
DOUBLE_TAP_INTERVAL = 0.4  # seconds for double-tap detection

# Text-to-speech (read selection/clipboard aloud).
# Primary engine: LemonFox TTS (cloud, natural voice). Fallback: macOS `say`.
TTS_VOICE = "Paulina (Enhanced)"  # fallback voice; run `say -v '?'` to list
TTS_SPEED_DEFAULT = 1.0  # playback speed multiplier (menu presets: 1.0-2.0)

# LemonFox (your own account — the key lives in .env, never committed)
LEMONFOX_API_KEY = os.getenv("LEMONFOX_API_KEY", "")
LEMONFOX_TTS_URL = "https://api.lemonfox.ai/v1/audio/speech"
LEMONFOX_VOICE = "dora"  # GOTCHA: an invalid voice does NOT error — it silently
                         # falls back to a default voice. Validate by ear.
# Menú "Voz de lectura" (validadas de oído). OJO: "santa" existe pero es
# INGLÉS americano (docs de LemonFox); no la pongas. La lista en español no está publicada, y el
# audio no sale igual dos veces: comparar huellas no prueba que una voz exista. Solo el oído.
LEMONFOX_VOICES = [("Dora", "dora"), ("Alex", "alex"), ("Noel", "noel")]

# Preparador de lectura (core/lectura.py): cómo se dicen las listas con número o
# letra ("ordinal": Primero… / Apartado A… · "numero": Uno… / A… · "pausa": nada), y las
# siglas que el corrector de macOS cree palabras pero deben deletrearse.
LECTURA_LISTAS = "numero"
LECTURA_SIGLAS = {"PDF", "URL", "HTML", "JSON", "CSS", "XML", "SMS", "USB", "GPS", "DNS",
                  "SQL", "API", "CRM", "IA", "UX", "UI", "KPI", "CEO", "CTO", "QA", "TV"}
LEMONFOX_LANGUAGE = "es"
LEMONFOX_TIMEOUT = 15.0  # seconds per chunk request
LEMONFOX_CHUNK_CHARS = 900  # split long texts into sentence chunks up to this size
LEMONFOX_DASHBOARD_URL = "https://www.lemonfox.ai/billing"
# Plan: $5/month = 10M credits = 2M TTS chars. Set the day YOUR cycle renews.
LEMONFOX_CHARS_PER_CYCLE = 2_000_000
LEMONFOX_CYCLE_DAY = 22

# Database (writable user data)
DB_PATH = os.path.join(_DATA_DIR, "transcriptions.db")

# Exported for other modules
APP_DATA_DIR = _DATA_DIR
