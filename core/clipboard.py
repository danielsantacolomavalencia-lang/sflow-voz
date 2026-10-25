import os
import subprocess
import time

from AppKit import (
    NSPasteboard,
    NSPasteboardTypeString,
    NSWorkspace,
    NSRunningApplication,
    NSApplicationActivateIgnoringOtherApps,
)

# Focus is tracked by PID, not by app name: two Chrome profiles are two
# processes sharing the name "Google Chrome" AND the bundle id, so
# `tell application "Google Chrome" to activate` is ambiguous — it can drop
# the caret in the other profile's window (or hang: asking that Chrome for
# its tabs blocked for minutes). A PID names exactly one window server client.
from dataclasses import dataclass, field


@dataclass
class Destino:
    """A dónde va un dictado: la app enfocada al presionar la tecla y el
    portapapeles de ese momento. Cada dictado lleva el suyo, así el pegado
    puede correr en segundo plano sin que el siguiente dictado lo pise."""
    pid: int | None = None
    app: str | None = None
    portapapeles: list | None = field(default=None, repr=False)

TRACE = os.getenv("SFLOW_TRACE") == "1"


def focus_now() -> str:
    """'AppName#pid' of whatever is frontmost — for diagnosing focus jumps."""
    try:
        a = NSWorkspace.sharedWorkspace().frontmostApplication()
        return f"{a.localizedName()}#{a.processIdentifier()}" if a else "?"
    except Exception:
        return "?"


def trace(tag: str):
    if TRACE:
        print(f"[traza {time.strftime('%H:%M:%S')}.{int(time.time()*1000)%1000:03d}] "
              f"{tag:22s} foco={focus_now()}", flush=True)


def _snapshot_clipboard() -> list | None:
    """All current clipboard items (text, images, files, etc.)."""
    try:
        pb = NSPasteboard.generalPasteboard()
        items = pb.pasteboardItems()
        if not items:
            return None
        snapshot = []
        for item in items:
            item_data = {}
            for ptype in item.types():
                data = item.dataForType_(ptype)
                if data:
                    item_data[ptype] = data.copy()
            if item_data:
                snapshot.append(item_data)
        return snapshot or None
    except Exception:
        return None


def _restore_clipboard(snapshot: list | None):
    """Put a saved snapshot back on the clipboard."""
    if not snapshot:
        return
    try:
        from AppKit import NSPasteboardItem
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        items = []
        for item_data in snapshot:
            pb_item = NSPasteboardItem.alloc().init()
            for ptype, data in item_data.items():
                pb_item.setData_forType_(data, ptype)
            items.append(pb_item)
        pb.writeObjects_(items)
    except Exception:
        pass


def get_clipboard_text() -> str:
    """Return the current clipboard text (empty string if none)."""
    try:
        pb = NSPasteboard.generalPasteboard()
        return pb.stringForType_(NSPasteboardTypeString) or ""
    except Exception:
        return ""


def get_selected_or_clipboard_text() -> str:
    """Text to read aloud: the CURRENT SELECTION if there is one, else clipboard.

    Simulates Cmd+C into the frontmost app and watches NSPasteboard's
    changeCount: if it moved, something was selected — grab it and restore
    the user's clipboard untouched. If it didn't move (no selection, or the
    keystroke failed), fall back to whatever the clipboard already had.

    Uses a LOCAL snapshot (not the module-level one shared with the dictation
    flow) so a read-aloud can never clobber a dictation in progress.
    """
    try:
        pb = NSPasteboard.generalPasteboard()
        before_count = pb.changeCount()

        # Local snapshot of all clipboard items (text, images, files…)
        snapshot = []
        for item in pb.pasteboardItems() or []:
            item_data = {}
            for ptype in item.types():
                data = item.dataForType_(ptype)
                if data:
                    item_data[ptype] = data.copy()
            if item_data:
                snapshot.append(item_data)

        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "c" using command down'],
            capture_output=True, timeout=3,
        )
        time.sleep(0.25)  # give the frontmost app time to publish the copy

        if pb.changeCount() != before_count:
            selected = pb.stringForType_(NSPasteboardTypeString) or ""
            # Put the user's clipboard back exactly as it was
            try:
                from AppKit import NSPasteboardItem
                pb.clearContents()
                items = []
                for item_data in snapshot:
                    pb_item = NSPasteboardItem.alloc().init()
                    for ptype, data in item_data.items():
                        pb_item.setData_forType_(data, ptype)
                    items.append(pb_item)
                if items:
                    pb.writeObjects_(items)
            except Exception:
                pass
            if selected.strip():
                return selected
    except Exception as e:
        print(f"[SFlow] captura de selección falló: {e}")

    return get_clipboard_text()


def capturar_destino() -> Destino:
    """Remember the focused app (by PID) and the clipboard, before recording.
    Rápido (solo NSWorkspace/NSPasteboard): puede correr en el hilo principal."""
    d = Destino(portapapeles=_snapshot_clipboard())
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is not None and app.bundleIdentifier() != "so.saasfactory.sflow":
            d.pid = int(app.processIdentifier())
            d.app = str(app.localizedName())
    except Exception as e:
        print(f"[SFlow] no se pudo leer la app activa: {e}")
    trace("guardo foco")
    return d


def _restore_focus(pid: int | None, timeout: float = 0.8) -> bool:
    """Bring the saved app back and CONFIRM it is frontmost. Never raises."""
    if pid is None:
        return False
    ws = NSWorkspace.sharedWorkspace()
    try:
        cur = ws.frontmostApplication()
        if cur is not None and int(cur.processIdentifier()) == pid:
            return True  # never left
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:  # the app quit while we transcribed
            return False
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.04)
            cur = ws.frontmostApplication()
            if cur is not None and int(cur.processIdentifier()) == pid:
                return True
    except Exception as e:
        print(f"[SFlow] fallo al devolver el foco: {e}")
    return False


def paste_text(text: str, destino: Destino) -> bool:
    """Paste text into the app focused before recording.

    Returns False WITHOUT pasting if the focus could not be restored: the text
    stays on the clipboard instead of landing in whatever window happens to be
    in front (which is how dictation ends up in the wrong Chrome profile).

    Corre en el hilo "sistema" (no en el principal): devolver el foco espera
    hasta 0.8 s y osascript tarda lo suyo; eso ya no congela el orbe.
    """
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)

    trace("antes de restaurar")
    ok = _restore_focus(destino.pid)
    trace("tras restaurar")

    if not ok:
        print(f"[SFlow] No pude devolver el foco a {destino.app!r}: el texto quedó "
              f"en el portapapeles, pégalo con Cmd+V.")
        return False  # clipboard keeps the transcription on purpose

    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to keystroke "v" using command down'],
        capture_output=True, timeout=3,
    )
    trace("tras pegar")

    time.sleep(0.15)  # let the paste land before putting the clipboard back
    _restore_clipboard(destino.portapapeles)
    return True
