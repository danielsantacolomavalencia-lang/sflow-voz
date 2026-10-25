"""El orbe de dos capas del pill, en QPainter.

Anatomía volumétrica, en cuatro pasadas por frame:
  1. cáscara TRASERA  — bañada por el resplandor del corazón
  2. resplandor interno del corazón
  3. la BRASA — convección: nace, asciende, titila, muere
  4. cáscara FRONTAL  — por delante del corazón, con contraluz y oclusión

Ese orden es lo que da el volumen: el corazón vive DENTRO de la cáscara.

Colores:
  · la persona elige una paleta (PALETAS: principal + acento) en el menú.
  · la PIEL dice el estado: reposo principal · escucha luna · pensando acento ·
    hablando principal con brasa blanca · error carmesí.
  · en 'pensando' piel y brasa se invierten para SEGUIR VISIBLE.
"""

import math
import random
import numpy as np
from PyQt6.QtGui import QPainter, QColor, QRadialGradient, QImage
from PyQt6.QtCore import Qt, QPointF, QRectF

TAU = math.pi * 2
_LUT_N = 128

def _lut(stops):
    g = np.linspace(0.0, 1.0, _LUT_N)
    out = np.empty((_LUT_N, 3), dtype=np.float64)
    pos = [s[0] for s in stops]
    cols = np.array([s[1] for s in stops], dtype=np.float64)
    out[:] = cols[-1]
    done = np.zeros(_LUT_N, dtype=bool)
    for i in range(len(stops) - 1):
        seg = (~done) & (g <= pos[i + 1])
        if seg.any():
            t = (g[seg] - pos[i]) / max(pos[i + 1] - pos[i], 1e-9)
            out[seg] = cols[i] + (cols[i + 1] - cols[i]) * t[:, None]
            done |= seg
    return out


# ── colores ─────────────────────────────────────────────────────────────
# Cada paleta: un color PRINCIPAL y un ACENTO, cada uno en tres tonos (claro, medio, hondo).
# La persona elige la paleta en el menú «Color del orbe». Escucha (blanco luna) y error
# (carmesí) son iguales en todas: así el estado se reconoce con cualquier color.
PALETAS = {
    "grafito":   ("Grafito y oro",    ((244, 247, 252), (190, 198, 212), (112, 120, 136)),
                                       ((255, 228, 160), (226, 176, 62), (150, 108, 22))),
    "esmeralda": ("Esmeralda y lila", ((110, 240, 200), (16, 185, 129), (8, 110, 84)),
                                       ((214, 200, 255), (160, 132, 240), (98, 70, 186))),
    "violeta":   ("Violeta y rosa",   ((215, 198, 255), (160, 118, 250), (96, 60, 190)),
                                       ((255, 176, 214), (236, 72, 153), (160, 30, 95))),
}
PALETA_DEFAULT = "grafito"

PIEL_LUNA = _lut([(0, (246, 248, 252)), (0.5, (210, 217, 228)), (1, (154, 162, 176))])
PIEL_CARMESI = _lut([(0, (250, 160, 178)), (0.5, (200, 30, 60)), (1, (148, 18, 44))])
# brasa: (color al fondo, color al frente) — el degradado de profundidad que le da textura al corazón
BRASA_LUNA = ((150, 158, 172), (255, 255, 255))


def _piel(tonos):
    claro, medio, hondo = tonos
    return _lut([(0, claro), (0.5, medio), (1, hondo)])


def _brasa(tonos):
    claro, _, hondo = tonos
    return (hondo, claro)


class Estado:
    """Perillas de movimiento + look de un estado (tabla sellada 22-ago)."""

    def __init__(self, piel, brasa, conv, flick, latA, latV, ondas, sincro,
                 brasaA, enc, rot):
        self.piel = piel
        self.brasa = brasa
        self.conv, self.flick = conv, flick
        self.latA, self.latV = latA, latV
        self.ondas, self.sincro = ondas, sincro
        self.brasaA, self.enc, self.rot = brasaA, enc, rot


# Multiplicadores globales del movimiento (calibrados a ojo)
FLAMA, CONVECCION, CORAZON_INT = 3.2, 2.7, 0.65

def _e(piel, brasa, conv, flick, latA, latV, ondas, sincro, brasaA, enc, rot):
    """Estado con los multiplicadores del lab ya aplicados."""
    return Estado(piel, brasa, conv * CONVECCION, flick * FLAMA, latA, latV,
                  ondas, sincro, brasaA * CORAZON_INT, enc, rot)

def estados_de(paleta=PALETA_DEFAULT):
    """Los cinco estados con los colores de una paleta. Pensando invierte principal y acento;
    hablando lleva la brasa blanca para distinguirse del reposo."""
    _, principal, acento = PALETAS.get(paleta, PALETAS[PALETA_DEFAULT])
    pp, pa = _piel(principal), _piel(acento)
    bp, ba = _brasa(principal), _brasa(acento)
    return {
        "reposo":       _e(pp,           ba,         1.0,  1.0, 0.10, 1.1, 0, 0, 1.00, 1.00, 0.10),
        "escucha":      _e(PIEL_LUNA,    ba,         0.45, 0.6, 0.05, 0.8, 1, 0, 0.85, 1.00, 0.06),
        "pensando":     _e(pa,           bp,         2.3,  2.0, 0.16, 1.9, 0, 0, 1.30, 1.00, 0.16),
        "respondiendo": _e(pp,           BRASA_LUNA, 1.4,  1.3, 0.06, 1.1, 0, 1, 1.20, 1.00, 0.30),
        "error":        _e(PIEL_CARMESI, ba,         0.15, 0.4, 0.04, 0.7, 0, 0, 0.70, 0.82, 0.03),
    }


ESTADOS = estados_de()


def _barajar(pts, semilla=20260901):
    """El motor toma M puntos EQUIESPACIADOS de la silueta: si van en orden de
    espiral, el salto dibuja sus brazos (el "molinete"). Barajados, la muestra
    cae repartida por todo el disco."""
    rng = np.random.default_rng(semilla)
    idx = rng.permutation(len(pts))
    return pts[idx]


def _desordenar(pts, cuanto, semilla=7771):
    """La espiral de Fibonacci reparte perfecto y se lee como retícula; el fuego
    del original es aleatorio. Un empujón por punto le devuelve lo orgánico."""
    if cuanto <= 0.001:
        return pts
    rng = np.random.default_rng(semilla)
    return pts + (rng.random(pts.shape) - 0.5) * cuanto


def forma_un_corazon(n=400, r=0.40, desorden=0.14):
    """Un disco único de puntos en [-1,1] — el corazón."""
    i = np.arange(n)
    rad = r * np.sqrt((i + 0.5) / n)
    ang = (math.pi * (3 - math.sqrt(5))) * i
    pts = np.stack([rad * np.cos(ang), rad * np.sin(ang)], axis=1)
    return _barajar(_desordenar(pts, desorden))


class VoiceSimulator:
    """Envolvente de voz con ritmo de palabras, para cuando no hay volumen real que medir."""

    def __init__(self):
        self.val = 0.0
        self.target = 0.55
        self.t_acc = 0.0
        self.t_next = 0.0
        self.phrase = 1.0
        self.phrase_next = 0.0

    def update(self, dt: float, speaking: bool) -> float:
        self.t_acc += dt
        if not speaking:
            self.val += (0.0 - self.val) * min(1.0, dt * 4)
            return 0.0 if self.val < 0.0006 else self.val
        if self.t_acc >= self.t_next:
            self.t_next = self.t_acc + 0.18 + random.random() * 0.26
            self.target = 0.42 + 0.45 * pow(random.random(), 1.2)
        if self.t_acc >= self.phrase_next:
            self.phrase_next = self.t_acc + 1.4 + random.random() * 3.0
            self.phrase = 0.42 if random.random() < 0.3 else 1.0
        self.val += (self.target * self.phrase - self.val) * min(1.0, dt * 5)
        return self.val


class OrbEngine:
    """El orbe. `estado` ∈ reposo | escucha | pensando | respondiendo | error."""

    # calibre del original: 72px → N=430, M=88, dot=0.7 (nuestro caso)
    def __init__(self, lado_px=70.0, forma=None, esc_mult=2.4, brasa_mult=3.2,
                 dot_mult=0.70, alpha_mult=1.0, dens_mult=1.10,
                 relieve=0.75, piso_env=0.20, halo=(255, 255, 255), halo_int=None,
                 glow=0.65, paleta=PALETA_DEFAULT):
        # relieve: el motor original aplasta la profundidad al 0.3 cuando la
        # brasa toma una forma, y de esa profundidad salen el TAMAÑO y el COLOR
        # de cada partícula — por eso salían todas iguales. A 1.0 vuelve el fuego.
        self.relieve = relieve
        self.piso_env = piso_env      # 0.35 original = brasa constante; 0 = nace y muere
        self.halo = halo              # aureola del pozo (blanca, no coral)
        from config import ORB_HALO_INT
        self.halo_int = ORB_HALO_INT if halo_int is None else halo_int
        self.alpha_mult = alpha_mult
        self.dot_mult = dot_mult
        self.esc_mult = esc_mult
        self.brasa_mult = brasa_mult
        self.dens_mult = dens_mult
        self.calma = 0.15   # cuánta convección conserva la brasa con forma
        self.lado = lado_px
        self.glow_mult = glow          # brillo global del lab (0.65)
        self.forma = forma if forma is not None else forma_un_corazon(n=560, r=0.40, desorden=0.14)
        self.estados = estados_de(paleta)
        self.estado_a = self.estado_b = "reposo"
        self.mix = 1.0
        self.rot_y = 0.0
        self.tilt_x = 0.42
        self._build(lado_px)

    def _calibrar(self, px):
        pts = [(72, 430, 88, 0.7), (200, 900, 260, 1.05), (740, 2296, 780, 1.9)]
        if px <= pts[0][0]:
            return pts[0][1], pts[0][2], pts[0][3]
        for i in range(len(pts) - 1):
            if px <= pts[i + 1][0]:
                t = (px - pts[i][0]) / (pts[i + 1][0] - pts[i][0])
                return (round(pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t),
                        round(pts[i][2] + (pts[i + 1][2] - pts[i][2]) * t),
                        pts[i][3] + (pts[i + 1][3] - pts[i][3]) * t)
        return pts[-1][1], pts[-1][2], pts[-1][3]

    def _build(self, lado):
        n, m, dot = self._calibrar(lado)
        # El CALIBRE del original no baja de 430/88 bajo 72px (satura en c[0]):
        # a tamaño pill queda apretado. Estos multiplicadores lo abren.
        n = max(120, int(n * self.dens_mult))
        m = max(24, int(m * self.brasa_mult))
        # puntoMult: escala el punto de la CÁSCARA y de la brasa
        # (antes solo la brasa, y la cáscara salía un 43% más gorda que el original)
        self.dot = dot * self.dot_mult
        self.R = lado * 0.33
        # finuras() para tamaño pequeño: oclusión 0.6, boost 1.25, triScale .078
        self.ocl, self.boost = 0.6, 1.25
        self.ts = self.R * 0.078
        self.oyv = -0.025 * self.R
        ts = self.ts
        self.tri = [(-8 * ts, 4 * ts + self.oyv), (4 * ts, -8 * ts + self.oyv),
                    (4 * ts, 4 * ts + self.oyv)]

        # cáscara: esfera de Fibonacci
        i = np.arange(n)
        yy = 1 - (i / max(n - 1, 1)) * 2
        rad = np.sqrt(np.maximum(0, 1 - yy * yy))
        th = (math.pi * (3 - math.sqrt(5))) * i
        self.px, self.py, self.pz = np.cos(th) * rad, yy, np.sin(th) * rad
        rng = np.random.default_rng(7)
        self.ph = rng.random(n) * TAU
        self.tw = 0.5 + rng.random(n) * 0.5
        self.n = n

        # brasa: M partículas vivas dentro del triángulo
        self.m = m
        self.brng = np.random.default_rng(23)
        bx, by = self._punto_en_triangulo(m)
        self.bx, self.by = bx, by
        self.bz = -0.55 + self.brng.random(m) * 0.7
        self.bph = self.brng.random(m) * TAU
        self.bvida = 2.5 + self.brng.random(m) * 3.5
        self.bedad = self.brng.random(m) * 4

    def _punto_en_triangulo(self, k):
        a, b, c = self.tri
        r1 = np.sqrt(self.brng.random(k))
        r2 = self.brng.random(k)
        u, v = 1 - r1, r2 * r1
        return (a[0] + (b[0] - a[0]) * u + (c[0] - a[0]) * v,
                a[1] + (b[1] - a[1]) * u + (c[1] - a[1]) * v)

    def _tri_dist(self, x, y):
        """Distancia con signo al triángulo (positiva fuera). Vectorizada."""
        a, b, c = self.tri
        def sd(p1, p2):
            ex, ey = p2[0] - p1[0], p2[1] - p1[1]
            ln = math.hypot(ex, ey) or 1
            return ((x - p1[0]) * ey - (y - p1[1]) * ex) / ln
        return -np.maximum(np.maximum(sd(a, b), sd(b, c)), sd(c, a))

    # ── estado ──
    def set_estado(self, e):
        if e not in self.estados or e == self.estado_b:
            return
        self.estado_a, self.estado_b, self.mix = self.estado_b, e, 0.0

    def _mezcla(self):
        A, B, m = self.estados[self.estado_a], self.estados[self.estado_b], self.mix
        lp = lambda a, b: a + (b - a) * m
        return {
            "lutA": A.piel, "lutB": B.piel, "m": m,
            "brasaA_col": A.brasa, "brasaB_col": B.brasa,
            "conv": lp(A.conv, B.conv), "flick": lp(A.flick, B.flick),
            "latA": lp(A.latA, B.latA), "latV": lp(A.latV, B.latV),
            "ondas": lp(A.ondas, B.ondas), "sincro": lp(A.sincro, B.sincro),
            "brasaA": lp(A.brasaA, B.brasaA), "enc": lp(A.enc, B.enc),
            "rot": lp(A.rot, B.rot),
        }

    def avanzar(self, dt, nivel=0.0):
        """Física: mezcla de estado, giro y convección de la brasa."""
        self.mix = min(1.0, self.mix + dt * 1.8)
        p = self._mezcla()
        self.rot_y += p["rot"] * dt
        conv = p["conv"]
        # convección: la brasa asciende, envejece y renace
        # 'calma': con la forma sostenida el original aquieta la convección
        # (1 - 0.85*morph). La dejamos abierta para poder avivar el fuego.
        calma = self.calma
        self.bedad += dt * conv * calma
        self.by -= (0.018 + 0.012 * (self.bz + 0.55)) * self.R * dt * conv * calma
        muertas = (self.bedad > self.bvida) | (
            self._tri_dist(self.bx / max(p["enc"], .01), self.by / max(p["enc"], .01)) < -0.004 * self.R)
        k = int(muertas.sum())
        if k:
            nx, ny = self._punto_en_triangulo(k)
            self.bx[muertas], self.by[muertas] = nx, ny
            self.bz[muertas] = -0.55 + self.brng.random(k) * 0.7
            self.bvida[muertas] = 2.5 + self.brng.random(k) * 3.5
            self.bedad[muertas] = 0.0
        return p

    # ── render ──
    def render(self, painter: QPainter, t, w, h, nivel=0.0, pozo=True):
        p = self._mezcla()
        cx, cy = w / 2.0, h / 2.0
        R = self.R
        m = p["m"]

        latido = 1 - p["latA"] + p["latA"] * math.sin(t * p["latV"] * (TAU / 2)) * 0.5 + p["latA"] * 0.5
        latido = latido * (1 - p["sincro"] * 0.5) + p["sincro"] * (0.72 + 0.55 * nivel)
        breathe = 1 + 0.03 * math.sin(t * 0.65)
        base_r = R * breathe

        if pozo:
            self._pozo(painter, cx, cy, base_r)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        painter.setPen(Qt.PenStyle.NoPen)

        cT, sT = math.cos(self.tilt_x), math.sin(self.tilt_x)
        cY, sY = math.cos(self.rot_y), math.sin(self.rot_y)
        fov = 3.6

        x = self.px * cY + self.pz * sY
        z = -self.px * sY + self.pz * cY
        y2 = self.py * cT - z * sT
        z2 = self.py * sT + z * cT
        persp = fov / (fov - z2 * 0.9)
        sx = cx + x * base_r * persp
        sy = cy + y2 * base_r * persp
        depth = 0.5 + 0.5 * z2
        fog = 0.35 + 0.65 * depth
        gg = np.clip((sy - (cy - base_r)) / (2 * base_r), 0, 1)
        idx = (gg * (_LUT_N - 1)).astype(np.int32)
        col = p["lutA"][idx] + (p["lutB"][idx] - p["lutA"][idx]) * m
        a = (0.12 + 0.52 * depth) * fog
        size = (0.5 + 1.05 * depth) * persp * self.dot
        a = a * (0.92 + 0.2 * np.sin(t * 0.9 + self.ph)) * (0.72 + 0.28 * self.tw)

        if p["ondas"] > 0.01:  # escucha: la piel recibe
            dn = np.minimum(1, np.hypot(sx - cx, sy - cy) / base_r)
            wv = np.power(0.5 + 0.5 * np.cos((dn * 2.8 + t * 0.75) * TAU), 4)
            a = a + 0.5 * wv * depth * p["ondas"]
            size = size * (1 + 0.45 * wv * p["ondas"])
        if p["sincro"] > 0.01:  # hablar: ondas salientes con la voz
            dn = np.minimum(1, np.hypot(sx - cx, sy - cy) / base_r)
            wv = np.power(0.5 + 0.5 * np.cos((dn * 2.8 - t * 1.5) * TAU), 4)
            a = a + 0.55 * wv * depth * p["sincro"] * (0.25 + 0.75 * nivel)
            size = size * (1 + 0.4 * wv * p["sincro"] * (0.25 + 0.75 * nivel))
        a = a * (1 + 0.45 * nivel * p["sincro"])
        size = size * (1 + 0.16 * nivel * p["sincro"])

        d = self._tri_dist(sx - cx, sy - cy)
        atras = z2 < 0

        # ── pasada 1: cáscara trasera (bañada por el resplandor del corazón)
        col_b = col.copy()
        banho = atras & (d > -0.1 * R)
        if banho.any():
            wb = np.minimum(1, (d[banho] + 0.1 * R) / (0.14 * R))[:, None] * 0.35
            objetivo = np.array([255.0, 120.0, 95.0])
            col_b[banho] = col_b[banho] + (objetivo - col_b[banho]) * wb
        a_b = np.minimum(1, a * self.boost)
        self._puntos(painter, sx, sy, size, a_b, col_b, atras & (a_b >= 0.03), w, h)

        # ── pasada 2: resplandor interno del corazón
        g = QRadialGradient(cx, cy + self.oyv, 0.52 * base_r)
        glow = min(1.0, 0.2 * latido * p["brasaA"])
        g.setColorAt(0.0, QColor(255, 105, 85, int(glow * 255)))
        g.setColorAt(1.0, QColor(255, 105, 85, 0))
        painter.fillRect(QRectF(cx - 0.52 * base_r, cy + self.oyv - 0.52 * base_r,
                                1.04 * base_r, 1.04 * base_r), g)

        # ── pasada 3: la brasa, tomando la forma de las dos bolitas
        self._brasa(painter, t, cx, cy, p, latido, nivel)

        # ── pasada 4: cáscara frontal (delante del corazón: contraluz + oclusión)
        col_f = col.copy()
        frente = ~atras
        ocluye = frente & (d > -0.01 * R)
        a_f = a.copy()
        if ocluye.any():
            wq = np.minimum(1, (d[ocluye] + 0.01 * R) / (0.03 * R))
            a_f[ocluye] = a_f[ocluye] * (1 - self.ocl * wq)
            col_f[ocluye] = col_f[ocluye] + (np.array([255.0, 140.0, 110.0]) - col_f[ocluye]) * (0.3 * wq)[:, None]
        halo = frente & (d <= -0.01 * R) & (d > -0.09 * R)
        if halo.any():
            wr = (1 - (-d[halo] - 0.01 * R) / (0.08 * R))[:, None]
            col_f[halo] = col_f[halo] + (np.array([255.0, 140.0, 110.0]) - col_f[halo]) * (0.18 * wr)
        a_f = np.minimum(1, a_f * self.boost)
        self._puntos(painter, sx, sy, size, a_f, col_f, frente & (a_f >= 0.03), w, h)

    def _pozo(self, painter, cx, cy, base_r):
        """La noche del orbe: el pozo oscuro + su aureola (rama 'plata' del original).

        El halo es un degradado ANULAR: canvas `createRadialGradient(cx,cy,
        atm*0.9, cx,cy, atm*1.24)`. Sus stops viven entre 0.9·atm y 1.24·atm,
        o sea pegados al borde de la esfera. Con un degradado desde el centro
        (el port anterior) el blanco caía DENTRO del orbe, tapado por las
        partículas — por eso el glow no se veía en el pill.
        Qt lo replica 1:1 con el radio focal de QRadialGradient."""
        atm = base_r * 1.04
        c = QPointF(cx, cy)
        painter.setPen(Qt.PenStyle.NoPen)
        g = QRadialGradient(c, atm)
        g.setColorAt(0.0, QColor(4, 6, 12, int(0.65 * 255)))
        g.setColorAt(0.85, QColor(6, 9, 17, int(0.42 * 255)))
        g.setColorAt(1.0, QColor(6, 9, 17, 0))
        painter.setBrush(g)
        painter.drawEllipse(c, atm, atm)
        hr, hg, hb = self.halo          # halo blanco
        hi = self.halo_int
        ga = QRadialGradient(c, atm * 1.24, c, atm * 0.9)   # anillo: focal 0.9·atm
        ga.setColorAt(0.0, QColor(hr, hg, hb, 0))
        ga.setColorAt(0.32, QColor(hr, hg, hb, int(0.16 * hi * 255)))
        ga.setColorAt(0.62, QColor(hr, hg, hb, int(0.06 * hi * 255)))
        ga.setColorAt(1.0, QColor(hr, hg, hb, 0))
        painter.setBrush(ga)
        painter.drawEllipse(c, atm * 1.24, atm * 1.24)

    def _brasa(self, painter, t, cx, cy, p, latido, nivel):
        fov = 3.6
        esc = 6.5 * self.ts * self.esc_mult
        k = np.arange(self.m)
        # la forma manda (identidad sostenida): cada partícula va a su punto
        tp = self.forma[(k / self.m * len(self.forma)).astype(int) % len(self.forma)]
        dbx = tp[:, 0] * esc
        dby = tp[:, 1] * esc + self.oyv
        # RELIEVE: el original comprime z a 0.3 con la forma sostenida, y de z
        # salen tamaño y color de cada partícula. A 1.0 vuelven las grandes,
        # las chicas y el degradado dorado→rojizo: el aspecto de fuego.
        z_uso = self.bz * self.relieve
        env = np.sin(np.pi * np.minimum(1, self.bedad / self.bvida))
        env = self.piso_env + (1.0 - self.piso_env) * env
        qpersp = fov / (fov - z_uso * 0.9)
        qsx = cx + dbx * qpersp
        qsy = cy + dby * qpersp
        depth_n = (z_uso + 0.55) / 0.7
        flick = 0.82 + 0.18 * np.sin(t * 7 * p["flick"] + self.bph)   # titileo por partícula
        qa = (0.2 + 0.8 * depth_n) * env * flick * latido * p["brasaA"] * self.alpha_mult
        qsize = (0.45 + 1.05 * depth_n) * qpersp * (self.dot * 0.9)
        # color: degradado de profundidad entre el par del estado (crossfade)
        cbA, cfA = p["brasaA_col"]
        cbB, cfB = p["brasaB_col"]
        m = p["m"]
        atras_c = np.array(cbA) + (np.array(cbB) - np.array(cbA)) * m
        frente_c = np.array(cfA) + (np.array(cfB) - np.array(cfA)) * m
        dn = np.clip(depth_n, 0, 1)[:, None]
        qcol = atras_c + (frente_c - atras_c) * dn
        vis = qa >= 0.02
        self._puntos(painter, qsx, qsy, np.maximum(qsize, 0.25), np.minimum(qa, 1), qcol, vis, None, None)

    @staticmethod
    def _puntos(painter, sx, sy, size, alpha, col, mask, w, h):
        if w is not None:
            mask = mask & (sx > -4) & (sx < w + 4) & (sy > -4) & (sy < h + 4)
        ci = col.astype(np.int32)
        ai = (alpha * 255).astype(np.int32)
        for i in np.nonzero(mask)[0]:
            painter.setBrush(QColor(int(ci[i, 0]), int(ci[i, 1]), int(ci[i, 2]), int(ai[i])))
            painter.drawEllipse(QPointF(sx[i], sy[i]), size[i], size[i])


def render_masked(engine, buf: QImage, t, nivel=0.0, dpr=1.0, pozo=True):
    """Dibuja el orbe en `buf` y lo recorta en círculo.

    El motor debe construirse con `lado_px = lado del búfer` (en puntos): así
    R = 0.33 × lado, la esfera ocupa el 66% del lienzo y el halo (1.24 × 1.04 × R
    ≈ 0.85 × radio del lienzo) cabe entero SIN tocar el filo. Un port anterior
    construía el motor con lado × 1.5: la esfera llenaba el pill, el halo se
    salía del búfer y hacía falta una máscara de desvanecido que se comía el
    borde del orbe — un "traslúcido desde el contorno" que se veía mal.

    El recorte final es el `border-radius: 50%` del <canvas>: un círculo duro
    antialiasado. Como nada llega al filo, no deja línea.
    """
    side = buf.width() / dpr
    buf.fill(Qt.GlobalColor.transparent)
    pt = QPainter(buf)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing)
    pt.scale(dpr, dpr)
    engine.render(pt, t, side, side, nivel=nivel, pozo=pozo)
    pt.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    pt.setPen(Qt.PenStyle.NoPen)
    pt.setBrush(QColor(0, 0, 0, 255))
    pt.drawEllipse(QPointF(side / 2.0, side / 2.0), side / 2.0, side / 2.0)
    pt.end()
