<p align="center">
  <img src="logo.png" width="120" alt="SFlow Logo">
</p>

<h1 align="center">SFlow</h1>

<p align="center">
  <strong>Open-source voice-to-text for macOS. Wispr Flow alternative at 99% lower cost.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/macOS-15%2B-blue?style=flat-square" alt="macOS">
  <img src="https://img.shields.io/badge/Python-3.12%2B-green?style=flat-square" alt="Python">
  <img src="https://img.shields.io/badge/STT-Groq%20Whisper-orange?style=flat-square" alt="Groq Whisper">
  <img src="https://img.shields.io/badge/Cost-%240.02%2Fhr-brightgreen?style=flat-square" alt="Cost">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="License">
</p>

---

## What is SFlow?

SFlow is a **system-wide voice-to-text tool** for macOS. Hold a hotkey, speak, release — your words appear wherever your cursor is. Any app, any text field, any language.

Built as a replacement for [Wispr Flow](https://wispr.com) ($15/month). SFlow uses [Groq's Whisper API](https://console.groq.com/docs/speech-to-text) at **~$0.02/hour** — that's roughly **$0.60/month** with heavy daily use.

### Features

- **Native macOS app** — lives in the menu bar, no terminal needed, starts with your Mac
- **System-wide dictation** — works in any app (VS Code, Chrome, Slack, Notes, etc.)
- **Two recording modes** — hold Ctrl+Shift (push-to-talk) or double-tap Ctrl (hands-free)
- **Floating pill UI** — minimal overlay with real-time audio visualization bars
- **No focus stealing** — pill floats above everything without interrupting your work (native macOS APIs)
- **Auto-paste** — text appears exactly where your cursor was
- **Web dashboard** — browse, search, and copy transcription history at `localhost:5678`
- **SQLite history** — every transcription saved locally with timestamp and duration
- **Multilingual** — supports all languages Whisper supports (English, Spanish, French, etc.)
- **First-run setup** — asks for your Groq API key on first launch, no config files to edit

---

## 🔊 SFlow con voz (2.2)

Además de dictar, SFlow **te habla**: lee en voz alta los resúmenes de Claude Code y narra demos en pantalla.

### Novedades 2.2

- **Voz directa** (`decir.py`, dentro de la skill `modo-voz`): Claude le manda cada frase a SFlow y espera a que termine de decirla. Es la forma preferida: no depende del gancho de las líneas 🔊 y lo que oyes va junto con lo que ves. Si Claude ya habló directo, el gancho no repite la respuesta (antes podía oírse dos veces).
- **Skill `modo-voz` al día:** la voz cuenta el trabajo «como un colega» (qué hizo, dónde quedó, qué necesita de ti) sin leer tablas ni código, y entiende la nota «[Modo coche]» del complemento de voz para Arbrain.
- **Voz por frase (opcional):** si quieres distinguir a Claude Code de otro agente que también habla con SFlow, crea `~/Library/Application Support/SFlow/voz_claude_code.json` con `{"voz": "noel"}` (o `"dora"`, `"alex"`). Sin ese archivo, todo usa la voz del menú.
- **Siglas en el diccionario de pronunciación:** una clave escrita toda en mayúsculas («TI», «IT») solo cambia cuando el texto la trae así («el área de TI» sí, «para ti» no). Palabras nuevas: Copilot, knowledge base, One-pager, Whitepaper.
- **Arreglo:** pide PyQt6 6.12 o más nuevo; con 6.10 y 6.11, abrir el menú del ícono podía cerrar SFlow en macOS 27. Si ya lo tenías instalado: `source venv/bin/activate && pip install -U -r requirements.txt && bash build.sh --install`.

### Lo de la 2.1

- **Voz que lee** con [LemonFox](https://www.lemonfox.ai) (voces Dora, Alex o Noel; `say` de macOS como respaldo). Volumen, velocidad y voz desde el menú.
- **Resúmenes de Claude Code en voz**: escribe «modo voz» en Claude Code y cada respuesta cierra con un resumen hablado (líneas que empiezan con 🔊).
- **Modo demo**: mientras Claude te muestra algo en pantalla, narra cada paso y espera a que la voz termine antes de actuar.
- **Preparador de lectura**: viñetas, listas, siglas y MAYÚSCULAS que se oyen bien; **diccionario de pronunciación** propio (`pronunciacion.json`, se administra con `herramientas/pronunciacion.py`).
- **Transcripción local** con mlx-whisper en Apple Silicon (sin internet, $0); Groq queda de respaldo automático.
- **Menú Micrófono** (eliges cuál, guardado por nombre) y aviso cuando un dictado sale en silencio.
- **Orbe de dos capas** en el pill: una cáscara de puntos con una brasa adentro que cambia según lo que hace SFlow (reposo, escucha, pensando, hablando, error) y sigue tu voz y la de la lectura. Color (Grafito y oro, Esmeralda y lila, Violeta y rosa) y tamaño desde el menú; con batería se queda quieto.

### Instalar en 3 pasos

1. **SFlow** (necesitas [Homebrew](https://brew.sh), Python 3.12+ y una [llave de Groq](https://console.groq.com/keys)):
   ```bash
   git clone https://github.com/danielsantacolomavalencia-lang/sflow-voz.git sflow
   cd sflow
   brew install portaudio
   python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
   bash build.sh --install
   ```
   La primera vez `build.sh` te pide crear un certificado de firma local llamado **SFlow Dev** (Acceso a Llaveros → Asistente de Certificados → Crear un certificado… → raíz autofirmada, tipo «Firma de código»). Sin él, macOS le quitaría los permisos a la app en cada actualización.
   Si ya tienes **SFlow v3** en Aplicaciones, `--install` no la reemplaza: usa `dist/SFlow.app` desde la carpeta.
2. **Tus llaves** (cada quien usa las suyas; nunca las subas a Git): las dos viven en `~/Library/Application Support/SFlow/.env`, una por renglón (mira `.env.example`):
   ```
   # para dictar (SFlow te la pide la primera vez que abre)
   GROQ_API_KEY=tu_llave_de_groq
   # opcional: para que te lea en voz alta
   LEMONFOX_API_KEY=tu_llave_de_lemonfox
   ```
   La de LemonFox se crea en [lemonfox.ai](https://www.lemonfox.ai/apis/keys). Sin ella, SFlow lee con la voz de macOS. Reinicia SFlow después de cambiar el archivo y, en su menú, marca «Leer en voz los resúmenes de Claude».
3. **Conectarlo a Claude Code y Arbrain** (instala las skills `modo-voz` y `modo-demo`, los ganchos que leen las líneas 🔊 y los accesos directos que usa Arbrain):
   ```bash
   /usr/bin/python3 claude_hooks/instalar.py
   ```
   Abre un chat nuevo de Claude Code y escribe **«modo voz»** (o «con demo» para que narre lo que te muestra). Para apagarlo: «apaga el modo voz». Para deshacerlo: `/usr/bin/python3 claude_hooks/instalar.py --quitar`.

> **Arbrain:** el chat de Arbrain no ejecuta los ganchos de Claude Code; ahí la voz la hace «Voz con SFlow» (Ajustes › Voz), propuesta al repo de Arbrain. Con el paso 3 hecho, Arbrain encuentra SFlow solo (`~/Library/Application Support/SFlow/voz.py`). En Claude Code funciona desde ya.

Mejoras de voz, lectura y orbe: Daniel Santacoloma, sobre el SFlow original de Daniel Carreón.

---

## Quick Start

### Prerequisites

- macOS 15+
- Python 3.12+
- [Homebrew](https://brew.sh)
- [Groq API key](https://console.groq.com/keys) (free tier available)

### Install (Desktop App — Recommended)

```bash
# Clone
git clone https://github.com/daniel-carreon/sflow.git
cd sflow

# System dependency
brew install portaudio

# Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Build the .app
bash build.sh

# Install (IMPORTANT: use ditto, not cp -r)
ditto dist/SFlow.app /Applications/SFlow.app
xattr -cr /Applications/SFlow.app
```

Open SFlow from Spotlight or `/Applications`. On first launch it asks for your [Groq API key](https://console.groq.com/keys).

### Install (Dev Mode)

```bash
git clone https://github.com/daniel-carreon/sflow.git
cd sflow
brew install portaudio
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and paste your GROQ_API_KEY
python3 main.py
```

---

## Usage

| Action | Shortcut |
|--------|----------|
| **Push-to-talk** | Hold `Ctrl+Shift`, speak, release |
| **Hands-free** | Double-tap `Ctrl` to start, tap `Ctrl` to stop |
| **View history** | Click "Abrir Dashboard" in menu bar, or `http://localhost:5678` |
| **Start with macOS** | Toggle in menu bar → "Iniciar con macOS" |
| **Quit** | Menu bar → "Salir" (or `Ctrl+C` in dev mode) |

### Pill States

| State | Visual |
|-------|--------|
| Idle | Small pill with logo |
| Recording | Expanded pill with animated audio bars |
| Processing | Spinning dots |
| Done | Green checkmark (auto-dismisses) |
| Error | Red X (auto-dismisses) |

---

## macOS Permissions

SFlow needs these permissions (System Settings → Privacy & Security):

1. **Accessibility** — for global hotkeys and auto-paste (add your Terminal app)
2. **Microphone** — for audio capture (requested automatically)
3. **Input Monitoring** — for keyboard listener (add your Terminal app)

---

## Architecture

```
Hotkey (pynput) → Audio Capture (sounddevice) → Groq Whisper API → Auto-Paste (AppleScript)
                        ↓                                                    ↓
                  Audio Bars (QPainter)                              SQLite Database
                        ↓                                                    ↓
                  Floating Pill (PyQt6 + PyObjC)                    Web Dashboard (Flask)
```

Key technical decisions:
- **PyObjC/AppKit** for native macOS window that floats without stealing focus
- **Qt QueuedConnection** for thread-safe signals between pynput and UI
- **AppleScript** for reliable paste (pbcopy + keystroke "v")
- **sounddevice + queue.Queue** for thread-safe audio visualization

---

## Build It Yourself with Claude

Want to build this from scratch? Copy [`PRP.md`](PRP.md) and paste it to [Claude](https://claude.ai) (or any AI assistant) with:

> "Build this project following the PRP phases. Execute all phases sequentially, validating each one before moving to the next."

The PRP contains the complete blueprint: architecture, gotchas, anti-patterns, and validation steps. It's designed so an AI agent can build the entire project in a single session.

See [`CLAUDE.md`](CLAUDE.md) for detailed development instructions and troubleshooting.

---

## Customization

All configuration lives in `config.py`:

```python
# Hotkey
DOUBLE_TAP_INTERVAL = 0.4  # seconds for double-tap detection

# UI
PILL_WIDTH_IDLE = 34        # pill width when idle (logo only)
PILL_WIDTH_RECORDING = 120  # pill width during recording
PILL_HEIGHT = 34            # pill height
PILL_MARGIN_BOTTOM = 14     # distance from bottom of screen

# Audio
SAMPLE_RATE = 16000         # 16kHz mono (optimal for speech)
NUM_BARS = 8                # number of visualizer bars
BAR_GAIN = 6.0              # bar sensitivity
BAR_DECAY = 0.80            # bar fall-off speed

# STT
GROQ_MODEL = "whisper-large-v3-turbo"  # fastest Groq model
```

---

## Cost Comparison

| | Wispr Flow | SFlow |
|---|---|---|
| Monthly cost | $15/month | ~$0.60/month* |
| Annual cost | $180/year | ~$7.20/year* |
| Data control | Third-party | Local |
| Customizable | No | Fully |
| Open source | No | Yes |

*\*Estimated for ~30 hours of transcription per month at $0.02/hour Groq pricing.*

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Pill doesn't appear | Grant Accessibility permission to your terminal |
| Pill steals focus | Verify PyObjC installed: `pip install pyobjc-framework-Cocoa` |
| Audio not captured | Check Microphone permissions + `brew list portaudio` |
| Paste goes to wrong app | This is the focus-steal issue — ensure PyObjC native setup works |
| Ctrl+C doesn't quit | Should work out of the box (SIGINT handler). Try `kill %1` |
| Dashboard not loading | Port auto-selects from 5678: `lsof -i :5678` |
| .app crashes (segfault) | Reinstall with `ditto` (not `cp -r`): `ditto dist/SFlow.app /Applications/SFlow.app` |
| .app blocked by macOS | Remove quarantine: `xattr -cr /Applications/SFlow.app` |

---

## License

MIT License. Do whatever you want with it.

---

<p align="center">
  Built with Claude Opus 4.6 in a single session.<br>
  <sub>From <a href="https://github.com/daniel-carreon">daniel-carreon</a> — <strong>S</strong><strong>f</strong>low</sub>
</p>
