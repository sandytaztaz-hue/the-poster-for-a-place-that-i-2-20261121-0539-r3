#!/usr/bin/env python3
"""ONDARA v2 — The Falls That Rise.
Painterly-realistic travel poster, pure Python 3.13 stdlib, deterministic.
Renders 1024x1024 PNG to /output/entry.png (and ./entry.png for preview).
No network, fixed seed, custom PNG writer, hand-coded serif bitmap font.
"""
import math
import os
import random
import struct
import zlib

W = H = 1024
SEED = 20261121
rng = random.Random(SEED)

# ---------------------------------------------------------------- canvas
px = bytearray(W * H * 3)


def setpx(x, y, r, g, b):
    if 0 <= x < W and 0 <= y < H:
        i = (y * W + x) * 3
        px[i] = r
        px[i + 1] = g
        px[i + 2] = b


def blend(x, y, r, g, b, a):
    """Alpha-blend one pixel; a in 0..255."""
    if a <= 0 or x < 0 or x >= W or y < 0 or y >= H:
        return
    i = (y * W + x) * 3
    if a >= 255:
        px[i] = r
        px[i + 1] = g
        px[i + 2] = b
        return
    ia = 255 - a
    px[i] = (r * a + px[i] * ia) // 255
    px[i + 1] = (g * a + px[i + 1] * ia) // 255
    px[i + 2] = (b * a + px[i + 2] * ia) // 255


def getpx(x, y):
    i = (y * W + x) * 3
    return px[i], px[i + 1], px[i + 2]


def lerp(a, b, t):
    return a + (b - a) * t


def lerp3(c1, c2, t):
    return (int(lerp(c1[0], c2[0], t)), int(lerp(c1[1], c2[1], t)),
            int(lerp(c1[2], c2[2], t)))


def clamp(v, lo=0, hi=255):
    return lo if v < lo else hi if v > hi else v


def sstep(t):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- noise
def _make_tile(cells, size=256):
    """Smooth value-noise tile (bilinear over a random grid)."""
    g = [[rng.random() for _ in range(cells + 1)] for _ in range(cells + 1)]
    out = [0] * (size * size)
    for yy in range(size):
        fy = yy / size * cells
        y0 = int(fy)
        ty = fy - y0
        y1 = y0 + 1
        for xx in range(size):
            fx = xx / size * cells
            x0 = int(fx)
            tx = fx - x0
            x1 = x0 + 1
            v = (g[y0][x0] * (1 - tx) * (1 - ty) + g[y0][x1] * tx * (1 - ty)
                 + g[y1][x0] * (1 - tx) * ty + g[y1][x1] * tx * ty)
            out[yy * size + xx] = int(v * 255)
    return out


NOISE_FINE = _make_tile(48)    # small brush texture
NOISE_COARSE = _make_tile(10)  # large tonal patches


def nf(x, y):
    return NOISE_FINE[(y & 255) * 256 + (x & 255)]


def nc(x, y):
    return NOISE_COARSE[(y & 255) * 256 + (x & 255)]


def tex(r, g, b, x, y, amt=14):
    """Painterly modulation: fine grain + coarse patch."""
    d = (nf(x, y) - 128) * amt // 128 + (nc(x, y) - 128) * (amt // 2) // 128
    return clamp(r + d), clamp(g + d), clamp(b + d)


# ---------------------------------------------------------------- shapes
def soft_disc(cx, cy, rad, color, alpha, hard=0.45):
    """Radial-soft disc (glow / sun / mist blob)."""
    r0 = int(rad)
    x0, x1 = max(0, cx - r0), min(W - 1, cx + r0)
    y0, y1 = max(0, cy - r0), min(H - 1, cy + r0)
    rr = rad * rad
    cr, cg, cb = color
    for y in range(y0, y1 + 1):
        dy = y - cy
        for x in range(x0, x1 + 1):
            dx = x - cx
            t = (dx * dx + dy * dy) / rr
            if t >= 1.0:
                continue
            a = alpha * (1.0 - sstep((t - hard) / (1.0 - hard))) if t > hard else alpha
            blend(x, y, cr, cg, cb, int(a))


def soft_ellipse(cx, cy, rx, ry, color, alpha, hard=0.5):
    r0x, r0y = int(rx), int(ry)
    x0, x1 = max(0, cx - r0x), min(W - 1, cx + r0x)
    y0, y1 = max(0, cy - r0y), min(H - 1, cy + r0y)
    cr, cg, cb = color
    for y in range(y0, y1 + 1):
        dy = (y - cy) / ry
        for x in range(x0, x1 + 1):
            dx = (x - cx) / rx
            t = dx * dx + dy * dy
            if t >= 1.0:
                continue
            a = alpha if t <= hard else alpha * (1.0 - sstep((t - hard) / (1.0 - hard)))
            blend(x, y, cr, cg, cb, int(a))


def hspan(y, x0, x1, color, a=255):
    """Solid horizontal span (fast slice when opaque)."""
    if y < 0 or y >= H:
        return
    x0 = max(0, x0)
    x1 = min(W - 1, x1)
    if x1 < x0:
        return
    r, g, b = color
    if a >= 255:
        row = y * W * 3
        px[row + x0 * 3: row + (x1 + 1) * 3] = bytes((r, g, b)) * (x1 - x0 + 1)
    else:
        for x in range(x0, x1 + 1):
            blend(x, y, r, g, b, a)


def vline(x, y0, y1, color, a=255):
    for y in range(max(0, y0), min(H - 1, y1) + 1):
        blend(x, y, color[0], color[1], color[2], a)


def blur_region(x0, y0, x1, y1, radius=3):
    """Box-blur a canvas region (softens hard pixel edges into paint)."""
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(W - 1, x1)
    y1 = min(H - 1, y1)
    w = x1 - x0 + 1
    h = y1 - y0 + 1
    rows = [bytearray(px[(y0 + i) * W * 3 + x0 * 3:(y0 + i) * W * 3 + (x1 + 1) * 3])
            for i in range(h)]
    out = [bytearray(w * 3) for _ in range(h)]
    for i in range(h):
        r = rows[i]
        for xx in range(w):
            xa = max(0, xx - radius)
            xb = min(w - 1, xx + radius)
            n = xb - xa + 1
            sr = sg = sb = 0
            for k in range(xa, xb + 1):
                sr += r[k * 3]
                sg += r[k * 3 + 1]
                sb += r[k * 3 + 2]
            out[i][xx * 3] = sr // n
            out[i][xx * 3 + 1] = sg // n
            out[i][xx * 3 + 2] = sb // n
    res = [bytearray(w * 3) for _ in range(h)]
    for xx in range(w):
        for yy in range(h):
            ya = max(0, yy - radius)
            yb = min(h - 1, yy + radius)
            n = yb - ya + 1
            sr = sg = sb = 0
            for k in range(ya, yb + 1):
                sr += out[k][xx * 3]
                sg += out[k][xx * 3 + 1]
                sb += out[k][xx * 3 + 2]
            res[yy][xx * 3] = sr // n
            res[yy][xx * 3 + 1] = sg // n
            res[yy][xx * 3 + 2] = sb // n
    for i in range(h):
        px[(y0 + i) * W * 3 + x0 * 3:(y0 + i) * W * 3 + (x1 + 1) * 3] = res[i]


def wavy_line(x0, y0, x1, y1, color, a, wob=3, step=4):
    """A slightly wobbling line (cracks, strata, path) — never ruler-straight."""
    n = max(2, int(math.hypot(x1 - x0, y1 - y0) // step))
    ph = rng.random() * 6.28
    for i in range(n + 1):
        t = i / n
        x = int(lerp(x0, x1, t) + math.sin(t * 9 + ph) * wob)
        y = int(lerp(y0, y1, t) + math.cos(t * 7 + ph) * wob)
        blend(x, y, color[0], color[1], color[2], a)
        blend(x + 1, y, color[0], color[1], color[2], a // 2)


# ================================================================ SKY
SKY_STOPS = [
    (0.00, (52, 44, 92)),    # soft indigo top
    (0.30, (128, 66, 92)),   # mauve
    (0.55, (196, 96, 70)),   # terracotta
    (0.78, (240, 148, 92)),  # peach
    (1.00, (252, 198, 118)), # gold at horizon
]
HORIZON = 592
SUN_CX, SUN_CY, SUN_R = 838, 552, 92


def sky_color(t):
    for i in range(len(SKY_STOPS) - 1):
        t0, c0 = SKY_STOPS[i]
        t1, c1 = SKY_STOPS[i + 1]
        if t0 <= t <= t1:
            return lerp3(c0, c1, (t - t0) / (t1 - t0))
    return SKY_STOPS[-1][1]


for y in range(16, HORIZON):
    t = (y - 16) / (HORIZON - 16)
    c = sky_color(t)
    # gentle horizontal haze variation so the sky is never a flat band
    r, g, b = c
    hspan(y, 16, W - 17, (r, g, b))
    # faint warm haze streaks
    if y % 3 == 0:
        hx = int(140 + 260 * math.sin(y * 0.021) + 120 * math.sin(y * 0.047 + 2))
        hspan(y, max(16, hx), min(W - 17, hx + 320), (252, 190, 120), 26)


# ---------------------------------------------------------------- clouds
def cloud(cx, cy, w, lit, belly):
    """A painterly cloud: cluster of soft blobs, lit top, shadow belly."""
    n = 7
    for i in range(n):
        ox = int((i - n // 2) * w / n * 0.85)
        oy = int(rng.randint(-6, 8))
        rw = int(w / n * (1.5 if i in (n // 2 - 1, n // 2) else 1.1))
        rh = int(rw * 0.42)
        soft_ellipse(cx + ox, cy + oy + 6, rw, rh, belly, 150, hard=0.3)
    for i in range(n):
        ox = int((i - n // 2) * w / n * 0.85)
        oy = int(rng.randint(-10, 0))
        rw = int(w / n * (1.4 if i in (n // 2 - 1, n // 2) else 1.0))
        rh = int(rw * 0.38)
        soft_ellipse(cx + ox, cy + oy, rw, rh, lit, 210, hard=0.35)
    # golden rim on the sun side
    soft_ellipse(cx + w // 3, cy - 4, w // 3, 7, (255, 200, 120), 120, hard=0.2)


cloud(210, 300, 260, (252, 196, 140), (168, 92, 88))
cloud(660, 250, 300, (252, 200, 146), (176, 96, 92))
cloud(880, 330, 210, (250, 188, 128), (170, 94, 90))
cloud(120, 430, 190, (246, 182, 122), (160, 88, 86))
cloud(760, 430, 240, (250, 192, 132), (172, 96, 90))
cloud(420, 170, 200, (244, 178, 128), (150, 84, 92))

# distant hazy headland on the horizon (left of the sun, never covering it)
for y in range(548, HORIZON):
    t = (y - 548) / (HORIZON - 548)
    edge = 640 + int(40 * math.sin(y * 0.09)) + int(t * 30)
    c = lerp3((150, 92, 96), (196, 128, 96), t)
    hspan(y, edge, 745, tex(c[0], c[1], c[2], 700, y, 10))

# ---------------------------------------------------------------- birds
def bird(x, y, s, a=200):
    for dx in range(-s, s + 1):
        yy = y - int(abs(dx) * 0.45)
        blend(x + dx, yy, 52, 40, 56, a)
        blend(x + dx, yy + 1, 52, 40, 56, a // 2)


bird(300, 260, 9)
bird(340, 282, 7)
bird(700, 300, 8)
bird(742, 322, 6)
bird(180, 340, 6)
bird(920, 380, 7)

# ================================================================ SEA
for y in range(HORIZON, 1008):
    t = (y - HORIZON) / (1008 - HORIZON)
    c = lerp3((66, 122, 132), (24, 66, 82), t)
    r, g, b = c
    hspan(y, 16, W - 17, (r, g, b))

# sun glitter path: widening golden dashes from horizon to bottom
for y in range(HORIZON + 2, 1000, 2):
    t = (y - HORIZON) / (1000 - HORIZON)
    half = int(30 + t * 110)
    cx = SUN_CX + int(10 * math.sin(y * 0.05))
    for x in range(cx - half, cx + half + 1, 6):
        w = rng.randint(5, 20)
        a = int(210 * (1 - abs(x - cx) / (half + 1)) * (0.5 + 0.5 * rng.random()))
        for xx in range(x, min(x + w, cx + half)):
            blend(xx, y, 255, 200, 115, a)
    # faint wide shimmer band under the path
    if y % 4 == 0:
        for x in range(cx - half - 30, cx + half + 31, 9):
            blend(x, y, 255, 190, 110, 40)

# wave strokes: layered horizontal brush strokes with foam tips
for y in range(HORIZON + 4, 1000, 4):
    nstrokes = 11
    for _ in range(nstrokes):
        x = rng.randint(30, 950)
        ln = rng.randint(18, 110)
        bright = rng.random() < 0.5
        if bright:
            col = (150, 205, 210)
            a = rng.randint(60, 150)
        else:
            col = (14, 48, 62)
            a = rng.randint(50, 120)
        yy = y + rng.randint(-2, 2)
        for xx in range(x, min(x + ln, W - 17)):
            blend(xx, yy, col[0], col[1], col[2], a)
        # foam tip
        if bright and rng.random() < 0.5:
            for xx in range(x + ln - 6, min(x + ln + 4, W - 17)):
                blend(xx, yy, 240, 248, 244, 150)

# foam lines near the shore / rocks
for x in range(16, W - 16, 6):
    yy = HORIZON + int(3 * math.sin(x * 0.03) + rng.randint(-2, 3))
    blend(x, yy, 238, 246, 242, rng.randint(90, 190))
    blend(x, yy + 1, 238, 246, 242, rng.randint(40, 110))

# reflections of the falls in the sea (soft vertical streaks below its base)
for y in range(HORIZON, 760, 4):
    t = (y - HORIZON) / (760 - HORIZON)
    half = int(70 * (1 - t * 0.5))
    for x in range(512 - half, 512 + half, 5):
        blend(x, y, 200, 230, 228, int(70 * (1 - t) * rng.random()))

# ---------------------------------------------------------------- sun
# wide halo
soft_disc(SUN_CX, SUN_CY, 280, (255, 150, 60), 130, hard=0.15)
soft_disc(SUN_CX, SUN_CY, 180, (255, 180, 80), 190, hard=0.25)
# disc with warm core
for y in range(SUN_CY - SUN_R, SUN_CY + SUN_R + 1):
    dy = (y - SUN_CY) / SUN_R
    hw = int(math.sqrt(max(0.0, 1 - dy * dy)) * SUN_R)
    t = (y - (SUN_CY - SUN_R)) / (2 * SUN_R)
    c = lerp3((255, 232, 150), (255, 128, 36), t)
    hspan(y, SUN_CX - hw, SUN_CX + hw, c)
# thin cloud streaks crossing the sun (like CINDARA)
for k, (sy, th) in enumerate([(548, 10), (566, 14), (584, 9)]):
    for x in range(SUN_CX - 150, SUN_CX + 150):
        wob = int(3 * math.sin(x * 0.05 + k * 2.1))
        blend(x, sy + wob, 214, 96, 58, 110)
        blend(x, sy + wob + 1, 214, 96, 58, 70)


# ================================================================ CLIFF (left)
def cliff_edge(y):
    """Right edge of the cliff at height y (wavy, never straight)."""
    base = 318 - (y - 300) * 0.06
    return int(base + 26 * math.sin(y * 0.045) + 12 * math.sin(y * 0.11 + 1.7))


for y in range(298, HORIZON + 2):
    x1 = cliff_edge(y)
    t = (y - 298) / (HORIZON - 298)
    # sunlit face (right, toward the sun) vs shadow face (left)
    for x in range(16, x1 + 1):
        u = (x - 16) / max(1, (x1 - 16))
        c = lerp3((84, 54, 48), (168, 104, 70), u * 0.9)
        # strata bands: darker horizontal layers
        if int(y / 26 + nc(x, y) / 90) % 3 == 0:
            c = lerp3(c, (70, 44, 40), 0.45)
        r, g, b = tex(c[0], c[1], c[2], x, y, 18)
        setpx(x, y, r, g, b)
    # warm rim light on the cliff edge
    blend(x1, y, 255, 178, 110, 150)
    blend(x1 - 1, y, 240, 150, 90, 90)

# cracks: dark wavy verticals
for _ in range(26):
    x0 = rng.randint(40, 300)
    y0 = rng.randint(300, 520)
    wavy_line(x0, y0, x0 + rng.randint(-30, 30), y0 + rng.randint(30, 90),
              (48, 30, 28), 170, wob=4)
# strata lines
for _ in range(18):
    y0 = rng.randint(320, 580)
    wavy_line(20, y0, cliff_edge(y0) - 4, y0 + rng.randint(-14, 14),
              (58, 36, 32), 130, wob=3)
# shrubs + small trees on the cliff (irregular, overlapping — never dots)
for _ in range(30):
    x = rng.randint(30, 300)
    y = rng.randint(320, 570)
    if x > cliff_edge(y) - 8:
        continue
    r0 = rng.randint(9, 20)
    col = (66, 104, 56) if rng.random() < 0.7 else (88, 118, 54)
    soft_ellipse(x, y, r0, int(r0 * 0.7), col, 215, hard=0.3)
    soft_ellipse(x - r0 // 2, y - 4, int(r0 * 0.7), int(r0 * 0.55),
                 (52, 86, 46), 200, hard=0.3)
    soft_ellipse(x + r0 // 3, y - 5, int(r0 * 0.6), int(r0 * 0.45),
                 (122, 152, 72), 150, hard=0.3)
# cliff path (light winding trail) with walkers
path_pts = [(60, 560), (110, 520), (90, 470), (150, 430), (130, 380), (190, 350)]
for i in range(len(path_pts) - 1):
    x0, y0 = path_pts[i]
    x1, y1 = path_pts[i + 1]
    wavy_line(x0, y0, x1, y1, (226, 196, 150), 200, wob=2)
    wavy_line(x0, y0 + 3, x1, y1 + 3, (226, 196, 150), 140, wob=2)

# ---------------------------------------------------------------- people
def person(x, y, s, shirt, pants, hat=None, pose="stand"):
    """A real small figure: head, hat, torso, legs, arms. s ~ height px."""
    head_r = max(2, s // 7)
    hx, hy = x, y - s + head_r
    # legs
    leg_h = max(3, s // 3)
    for dx in (-1, 1):
        for yy in range(y - leg_h, y):
            blend(x + dx * 2, yy, pants[0], pants[1], pants[2], 255)
    # torso
    torso_top = hy + head_r
    torso_bot = y - leg_h
    for yy in range(torso_top, torso_bot + 1):
        wdt = max(2, s // 5)
        for xx in range(x - wdt // 2, x + wdt // 2 + 1):
            blend(xx, yy, shirt[0], shirt[1], shirt[2], 255)
    # arms
    if pose == "up":
        for yy in range(torso_top, torso_top - s // 3, -1):
            blend(x + s // 4, yy, shirt[0], shirt[1], shirt[2], 255)
        blend(x + s // 4, torso_top - s // 3, 224, 178, 140, 255)
    elif pose == "row":
        for xx in range(x + 2, x + s // 2):
            blend(xx, torso_top + 2, shirt[0], shirt[1], shirt[2], 255)
    else:
        for yy in range(torso_top + 1, torso_bot):
            blend(x - s // 4 - 1, yy, shirt[0], shirt[1], shirt[2], 220)
            blend(x + s // 4 + 1, yy, shirt[0], shirt[1], shirt[2], 220)
    # head
    soft_disc(hx, hy, head_r, (224, 178, 140), 255, hard=0.6)
    # hat
    if hat:
        for xx in range(hx - head_r - 2, hx + head_r + 3):
            blend(xx, hy - head_r + 1, hat[0], hat[1], hat[2], 255)
        soft_disc(hx, hy - head_r + 1, head_r - 1, hat, 255, hard=0.6)


# walkers on the cliff path
person(104, 522, 16, (196, 60, 50), (52, 48, 60), hat=(226, 190, 110), pose="walk")
person(146, 432, 15, (60, 110, 150), (48, 44, 52), pose="walk")
person(132, 384, 14, (232, 200, 120), (70, 60, 52), hat=(150, 60, 50), pose="stand")

# ---------------------------------------------------------------- ledge cottages (real stone/wood, NOT white squares)
def cottage(x, y, w, h, wall, roof, lit=True):
    """A small stone/wood cottage with terracotta roof, lit windows, chimney."""
    # wall with wobbly edges + stone texture
    for yy in range(y, y + h):
        wob = int(2 * math.sin(yy * 0.3))
        for xx in range(x + wob, x + w + wob):
            r, g, b = tex(wall[0], wall[1], wall[2], xx, yy, 16)
            # stone courses
            if (yy // 7) % 2 == 0 and nf(xx, yy) > 200:
                r, g, b = clamp(r - 18), clamp(g - 14), clamp(b - 10)
            setpx(xx, yy, r, g, b)
    # timber framing
    vline(x + 3, y, y + h - 1, (96, 66, 44), 160)
    vline(x + w - 3, y, y + h - 1, (96, 66, 44), 160)
    # roof: trapezoid with wavy eave, terracotta shading
    rh = h // 2 + 6
    for yy in range(y - rh, y + 1):
        t = (yy - (y - rh)) / rh
        half = int(w * 0.5 + 10 - t * 6)
        cx = x + w // 2
        c = lerp3((214, 120, 70), (150, 66, 40), t)
        for xx in range(cx - half, cx + half + 1):
            r, g, b = tex(c[0], c[1], c[2], xx, yy, 14)
            setpx(xx, yy, r, g, b)
    # roof ridge highlight
    hspan(y - rh, x + w // 2 - 8, x + w // 2 + 8, (240, 160, 100), 200)
    # chimney + thin smoke
    chx = x + w - 12
    for yy in range(y - rh - 8, y - rh + 10):
        for xx in range(chx, chx + 8):
            setpx(xx, yy, *tex(120, 88, 70, xx, yy, 10))
    for i in range(5):
        soft_disc(chx + 4 + i * 3, y - rh - 14 - i * 7, 5 + i, (240, 232, 220), 60, hard=0.2)
    # door
    for yy in range(y + h - 16, y + h):
        for xx in range(x + w // 2 - 4, x + w // 2 + 5):
            setpx(xx, yy, *tex(96, 60, 38, xx, yy, 8))
    # windows: warm lit glass with dark mullions
    for wx in (x + 6, x + w - 16):
        for yy in range(y + 8, y + 20):
            for xx in range(wx, wx + 10):
                if lit:
                    setpx(xx, yy, 255, 198, 96)
                else:
                    setpx(xx, yy, 52, 48, 58)
        vline(wx + 4, y + 8, y + 19, (70, 50, 36), 220)
        hspan(y + 13, wx, wx + 9, (70, 50, 36), 220)
        # glow halo
        soft_ellipse(wx + 5, y + 14, 12, 10, (255, 190, 90), 60, hard=0.2)
    # flower pot by the door
    soft_disc(x + w // 2 + 12, y + h - 4, 4, (178, 84, 50), 255, hard=0.5)
    soft_disc(x + w // 2 + 12, y + h - 8, 5, (200, 70, 90), 230, hard=0.4)


cottage(52, 470, 62, 46, (196, 158, 118), (178, 84, 50))
cottage(150, 540, 58, 44, (178, 140, 104), (168, 78, 46))
cottage(96, 380, 54, 40, (206, 170, 128), (182, 88, 52), lit=False)

# ================================================================ FALLS
# The colossal waterfall flowing UP: base at the sea, dissolving into mist
# at the top. Soft noisy edges, vertical streaks, foam, golden spray.
FALL_TOP_Y = 300
FALL_BASE_Y = 604


def falls_center(y):
    t = (y - FALL_TOP_Y) / (FALL_BASE_Y - FALL_TOP_Y)
    return 512 - int(48 * (1 - t))  # arcs slightly left toward the top


def falls_half(y):
    t = (y - FALL_TOP_Y) / (FALL_BASE_Y - FALL_TOP_Y)
    return int(58 + 34 * t)  # wider at the base


# under-mist shadow behind the column
soft_ellipse(500, 430, 95, 170, (120, 150, 168), 70, hard=0.2)

for y in range(FALL_TOP_Y, FALL_BASE_Y + 1):
    t = (y - FALL_TOP_Y) / (FALL_BASE_Y - FALL_TOP_Y)
    cx = falls_center(y)
    half = falls_half(y)
    for x in range(cx - half - 14, cx + half + 15):
        dx = (x - cx) / half
        edge = 1.0 - sstep((abs(dx) - 0.72) / 0.28)  # soft edge
        if edge <= 0:
            continue
        # vertical streak noise: water rushing upward
        streak = nf(x * 3, y // 3) / 255.0
        streak2 = nc(x, y // 2) / 255.0
        # sunlit side (right) vs shadow side (left)
        side = 0.5 + 0.5 * dx
        base = lerp3((110, 178, 192), (238, 246, 246), 0.35 + 0.5 * streak)
        base = lerp3(base, (255, 214, 140), side * 0.35 * streak2)
        # shadow side darker teal
        base = lerp3((70, 128, 150), base, 0.35 + 0.6 * side)
        # mist fade near the top
        topfade = sstep((y - FALL_TOP_Y) / 70.0)
        a = int(edge * (0.55 + 0.45 * streak) * topfade * 255)
        blend(x, y, base[0], base[1], base[2], a)
    # bright foam streaks running up the column
    for _ in range(3):
        sx = cx + rng.randint(-half + 8, half - 8)
        ln = rng.randint(14, 46)
        yy = y
        for k in range(ln):
            blend(sx + int(2 * math.sin(k * 0.4)), yy - k, 250, 252, 248,
                  int(200 * (1 - k / ln)))

# soften the streak lines into flowing water (painterly, not a curtain)
blur_region(398, 288, 642, 612, radius=2)

# foam ring at the base where the falls meet the sea
for a in range(0, 360, 4):
    rad = math.radians(a)
    ex = int(512 + math.cos(rad) * 92)
    ey = int(604 + math.sin(rad) * 16)
    soft_disc(ex, ey, rng.randint(5, 11), (244, 250, 248), 170, hard=0.3)
# churning foam at the base
for _ in range(90):
    x = rng.randint(430, 595)
    y = rng.randint(592, 622)
    soft_disc(x, y, rng.randint(3, 8), (248, 252, 250), rng.randint(90, 200), hard=0.3)

# golden spray droplets catching the sunset light (upward direction!)
for _ in range(260):
    # denser near the column, scattered upward above the top
    if rng.random() < 0.6:
        x = rng.randint(400, 620)
        y = rng.randint(FALL_TOP_Y - 40, FALL_BASE_Y)
    else:
        x = rng.randint(360, 660)
        y = rng.randint(FALL_TOP_Y - 90, FALL_TOP_Y + 40)
    r0 = rng.randint(1, 3)
    gold = rng.random() < 0.55
    col = (255, 214, 130) if gold else (240, 248, 250)
    soft_disc(x, y, r0, col, rng.randint(140, 255), hard=0.4)

# mist cloud where the falls dissolve into the sky
for i in range(16):
    mx = 470 + rng.randint(-70, 70)
    my = FALL_TOP_Y - 6 + rng.randint(-26, 22)
    soft_ellipse(mx, my, rng.randint(26, 58), rng.randint(12, 24),
                 (250, 238, 224), rng.randint(90, 170), hard=0.25)
for i in range(8):
    mx = 470 + rng.randint(-60, 60)
    my = FALL_TOP_Y - 20 + rng.randint(-14, 10)
    soft_ellipse(mx, my, rng.randint(18, 34), rng.randint(8, 14),
                 (255, 214, 140), rng.randint(70, 130), hard=0.25)

# ================================================================ TOWN (bottom shore)
# shore strip: warm sand/earth with texture, curving along the bottom
for y in range(812, 1008):
    t = (y - 812) / (1008 - 812)
    shore_top = 812 + int(26 * math.sin(y * 0.02))
    for x in range(16, W - 16):
        if y < 812 + int(30 * math.sin(x * 0.012) + 14):
            continue
        c = lerp3((196, 158, 112), (150, 110, 76), t)
        r, g, b = tex(c[0], c[1], c[2], x, y, 16)
        setpx(x, y, r, g, b)

# houses along the shore — real stone/wood, varied, lit windows
HOUSES = [
    (30, 872, 96, 78, (206, 168, 122), (172, 80, 48)),
    (140, 888, 84, 66, (186, 146, 106), (182, 88, 52)),
    (238, 862, 104, 84, (220, 186, 140), (168, 76, 44)),
    (356, 890, 80, 62, (178, 138, 100), (176, 82, 50)),
    (620, 868, 98, 80, (212, 174, 126), (170, 78, 46)),
    (730, 888, 88, 64, (190, 152, 110), (180, 86, 50)),
    (830, 862, 92, 82, (216, 180, 134), (172, 80, 48)),
    (934, 886, 74, 60, (184, 144, 104), (178, 84, 50)),
]


def town_house(x, y, w, h, wall, roof):
    # wall with wobbly edges + plaster/stone texture + vertical shading
    for yy in range(y, y + h):
        wob = int(2 * math.sin(yy * 0.25 + x))
        vt = (yy - y) / max(1, h)  # 0 top -> 1 bottom
        for xx in range(x + wob, x + w + wob):
            r, g, b = tex(wall[0], wall[1], wall[2], xx, yy, 22)
            if (yy // 8) % 2 == 0 and nf(xx, yy) > 205:
                r, g, b = clamp(r - 16), clamp(g - 12), clamp(b - 8)
            # warm light from above, shadow toward the base
            sh = 1.0 - 0.28 * vt
            r, g, b = int(r * sh), int(g * sh), int(b * sh)
            setpx(xx, yy, r, g, b)
    # ground shadow at the base of the house
    hspan(y + h - 2, x, x + w - 1, (60, 40, 30), 110)
    hspan(y + h - 1, x, x + w - 1, (60, 40, 30), 90)
    # timber beams
    vline(x + 4, y, y + h - 1, (96, 66, 44), 150)
    vline(x + w - 4, y, y + h - 1, (96, 66, 44), 150)
    hspan(y + h // 2, x, x + w - 1, (96, 66, 44), 120)
    # roof: sloped with wavy eave + terracotta shading + tile lines
    rh = h // 2 + 10
    for yy in range(y - rh, y + 1):
        t = (yy - (y - rh)) / rh
        half = int(w * 0.5 + 12 - t * 8)
        cx = x + w // 2
        c = lerp3((218, 124, 72), (148, 64, 38), t)
        for xx in range(cx - half, cx + half + 1):
            r, g, b = tex(c[0], c[1], c[2], xx, yy, 20)
            if (xx // 9) % 2 == 0 and nf(xx, yy) > 190:
                r, g, b = clamp(r - 14), clamp(g - 10), clamp(b - 6)
            setpx(xx, yy, r, g, b)
    # tile courses: horizontal wavy shadow lines across the roof
    for ty in range(y - rh + 8, y - 2, 9):
        t = (ty - (y - rh)) / rh
        half = int(w * 0.5 + 12 - t * 8)
        wavy_line(x + w // 2 - half + 2, ty, x + w // 2 + half - 2, ty,
                  (120, 52, 30), 150, wob=2)
    # eave shadow under the roof
    hspan(y, x, x + w - 1, (90, 50, 34), 150)
    hspan(y + 1, x, x + w - 1, (90, 50, 34), 90)
    hspan(y - rh, x + w // 2 - 10, x + w // 2 + 10, (244, 164, 102), 200)
    # chimney + smoke
    chx = x + w - 16
    for yy in range(y - rh - 10, y - rh + 12):
        for xx in range(chx, chx + 9):
            setpx(xx, yy, *tex(118, 86, 68, xx, yy, 10))
    for i in range(4):
        soft_disc(chx + 5 + i * 4, y - rh - 16 - i * 8, 5 + i, (242, 234, 222), 55, hard=0.2)
    # door
    for yy in range(y + h - 20, y + h):
        for xx in range(x + w // 2 - 5, x + w // 2 + 6):
            setpx(xx, yy, *tex(94, 58, 36, xx, yy, 8))
    # windows: some lit warm, some dark
    nwin = max(2, w // 34)
    for k in range(nwin):
        wx = x + 8 + k * ((w - 26) // max(1, nwin - 1)) if nwin > 1 else x + 8
        wy = y + 10 + (k % 2) * 20
        lit = rng.random() < 0.75
        for yy in range(wy, wy + 13):
            for xx in range(wx, wx + 12):
                if lit:
                    setpx(xx, yy, 255, 198, 96)
                else:
                    setpx(xx, yy, 54, 50, 60)
        vline(wx + 5, wy, wy + 12, (70, 50, 36), 220)
        hspan(wy + 6, wx, wx + 11, (70, 50, 36), 220)
        if lit:
            soft_ellipse(wx + 6, wy + 6, 14, 11, (255, 190, 90), 55, hard=0.2)
    # flower box under one window
    soft_disc(x + 14, y + 30, 5, (178, 84, 50), 255, hard=0.5)
    for dx in (-3, 0, 3):
        soft_disc(x + 14 + dx, y + 26, 3, (214, 80, 96), 230, hard=0.4)


for hx, hy, hw, hh, wall, roof in HOUSES:
    town_house(hx, hy, hw, hh, wall, roof)

# string lights between the town houses
for i in range(len(HOUSES) - 1):
    x0 = HOUSES[i][0] + HOUSES[i][2] // 2
    y0 = HOUSES[i][1] - HOUSES[i][3] // 2 - 8
    x1 = HOUSES[i + 1][0] + HOUSES[i + 1][2] // 2
    y1 = HOUSES[i + 1][1] - HOUSES[i + 1][3] // 2 - 8
    sag = 26
    for t_i in range(21):
        t = t_i / 20
        x = int(lerp(x0, x1, t))
        y = int(lerp(y0, y1, t) + sag * 4 * t * (1 - t))
        blend(x, y, 120, 90, 60, 200)
        if t_i % 3 == 1:
            soft_disc(x, y + 3, 3, (255, 200, 110), 255, hard=0.5)
            soft_disc(x, y + 3, 7, (255, 190, 100), 90, hard=0.2)

# ---------------------------------------------------------------- pier + lanterns
# wooden pier running from the shore into the sea (diagonal, planked)
PIER = [(150, 1008), (210, 940), (280, 880), (350, 824), (420, 772)]
for i in range(len(PIER) - 1):
    x0, y0 = PIER[i]
    x1, y1 = PIER[i + 1]
    n = 24
    for k in range(n + 1):
        t = k / n
        cx = int(lerp(x0, x1, t))
        cy = int(lerp(y0, y1, t))
        wdt = int(lerp(52, 34, t))
        for yy in range(cy - 2, cy + 3):
            for xx in range(cx - wdt // 2, cx + wdt // 2 + 1):
                r, g, b = tex(146, 102, 62, xx, yy, 16)
                setpx(xx, yy, r, g, b)
        # plank seams
        if k % 3 == 0:
            vline(cx, cy - 2, cy + 2, (96, 64, 38), 160)
# pier posts
for (pxx, pyy) in [(170, 972), (240, 908), (310, 852), (380, 800), (415, 776)]:
    for yy in range(pyy - 26, pyy + 6):
        for xx in range(pxx - 3, pxx + 4):
            setpx(xx, yy, *tex(110, 76, 46, xx, yy, 10))


def paper_lantern(x, y):
    """Glowing paper lantern with halo, ribs and tassel."""
    soft_disc(x, y, 36, (255, 150, 50), 140, hard=0.15)
    soft_disc(x, y, 18, (255, 190, 90), 190, hard=0.25)
    # lantern body: warm orange ellipse with ribs
    for yy in range(y - 11, y + 12):
        dy = (yy - y) / 11
        hw = int(math.sqrt(max(0.0, 1 - dy * dy)) * 12)
        for xx in range(x - hw, x + hw + 1):
            c = lerp3((255, 214, 130), (238, 120, 40), abs(xx - x) / 12)
            setpx(xx, yy, *tex(c[0], c[1], c[2], xx, yy, 8))
    for dx in (-6, 0, 6):
        vline(x + dx, y - 10, y + 10, (180, 90, 30), 180)
    hspan(y - 11, x - 5, x + 5, (150, 80, 30), 255)
    # tassel
    for yy in range(y + 12, y + 20):
        blend(x, yy, 200, 60, 50, 230)


# lanterns hanging from pier posts
for (lx, ly) in [(170, 940), (240, 876), (310, 820), (380, 768), (415, 744)]:
    vline(lx, ly - 32, ly - 12, (90, 62, 40), 220)
    paper_lantern(lx, ly)
# one big lantern at the pier end
paper_lantern(428, 748)

# couple on the pier looking UP at the falls
person(300, 848, 26, (196, 60, 50), (52, 48, 60), hat=(226, 190, 110), pose="up")
person(328, 850, 26, (240, 230, 210), (60, 58, 66), pose="up")
# walkers on the shore
person(560, 940, 22, (60, 110, 150), (48, 44, 52), pose="walk")
person(592, 946, 20, (232, 200, 120), (70, 60, 52), hat=(150, 60, 50), pose="stand")
person(702, 950, 21, (120, 60, 90), (52, 48, 56), pose="walk")

# ---------------------------------------------------------------- boats
def sailboat(x, y, s, sail=(246, 238, 214)):
    # hull: curved, wood with shading
    for yy in range(y, y + s // 3 + 1):
        t = (yy - y) / (s // 3)
        half = int(s * 0.42 * (1 - t * 0.35))
        c = lerp3((120, 74, 44), (74, 46, 30), t)
        for xx in range(x - half, x + half + 1):
            setpx(xx, yy, *tex(c[0], c[1], c[2], xx, yy, 10))
    # mast + sail
    vline(x, y - s, y, (70, 52, 38), 255)
    for yy in range(y - s, y):
        t = (yy - (y - s)) / s
        wdt = int(s * 0.42 * (1 - t))
        for xx in range(x + 2, x + 2 + wdt):
            setpx(xx, yy, *tex(sail[0], sail[1], sail[2], xx, yy, 8))
    # tiny sailor
    person(x - 4, y - 2, 10, (196, 60, 50), (52, 48, 60), pose="stand")


sailboat(690, 700, 52)
sailboat(880, 660, 40, sail=(250, 220, 170))
sailboat(150, 720, 44)


def rowboat(x, y, s):
    for yy in range(y, y + s // 4 + 1):
        t = (yy - y) / (s // 4)
        half = int(s * 0.4 * (1 - t * 0.3))
        c = lerp3((140, 92, 54), (88, 56, 34), t)
        for xx in range(x - half, x + half + 1):
            setpx(xx, yy, *tex(c[0], c[1], c[2], xx, yy, 10))
    # fisherman rowing
    person(x, y - 2, 16, (60, 110, 150), (48, 44, 52), hat=(226, 190, 110), pose="row")
    # oar
    wavy_line(x + 8, y - 10, x + 26, y + 4, (90, 64, 40), 230, wob=1)


rowboat(470, 760, 64)
rowboat(250, 800, 52)

# ================================================================ TEXT
GLYPHS = {
    "O": [".XXXXXX.", "XX....XX", "XX....XX", "XX....XX", "XX....XX",
          "XX....XX", "XX....XX", "XX....XX", "XX....XX", "XX....XX",
          "XX....XX", ".XXXXXX."],
    "N": ["XX....XX", "XXX...XX", "XXX...XX", "XXXX..XX", "XXXX..XX",
          "XX.XX.XX", "XX..XXXX", "XX..XXXX", "XX...XXX", "XX...XXX",
          "XX....XX", "XX....XX"],
    "D": ["XXXXXX..", "XX...XXX", "XX....XX", "XX....XX", "XX....XX",
          "XX....XX", "XX....XX", "XX....XX", "XX....XX", "XX....XX",
          "XX...XXX", "XXXXXX.."],
    "A": ["...XX...", "..XXXX..", "..XXXX..", ".XX..XX.", ".XX..XX.",
          "XX....XX", "XX....XX", "XXXXXXXX", "XX....XX", "XX....XX",
          "XX....XX", "XX....XX"],
    "R": ["XXXXXX..", "XX...XXX", "XX....XX", "XX....XX", "XX....XX",
          "XXXXXX..", "XX..XX..", "XX...XX.", "XX....XX", "XX....XX",
          "XX....XX", "XX....XX"],
    "T": ["XXXXXXXX", "XXXXXXXX", "...XX...", "...XX...", "...XX...",
          "...XX...", "...XX...", "...XX...", "...XX...", "...XX...",
          "...XX...", "...XX..."],
    "H": ["XX....XX", "XX....XX", "XX....XX", "XX....XX", "XX....XX",
          "XXXXXXXX", "XX....XX", "XX....XX", "XX....XX", "XX....XX",
          "XX....XX", "XX....XX"],
    "E": ["XXXXXXXX", "XXXXXXXX", "XX......", "XX......", "XX......",
          "XXXXXX..", "XX......", "XX......", "XX......", "XX......",
          "XXXXXXXX", "XXXXXXXX"],
    "F": ["XXXXXXXX", "XXXXXXXX", "XX......", "XX......", "XX......",
          "XXXXXX..", "XX......", "XX......", "XX......", "XX......",
          "XX......", "XX......"],
    "L": ["XX......", "XX......", "XX......", "XX......", "XX......",
          "XX......", "XX......", "XX......", "XX......", "XX......",
          "XXXXXXXX", "XXXXXXXX"],
    "S": [".XXXXXX.", "XX....XX", "XX......", "XX......", ".XXXXXX.",
          "......XX", "......XX", "......XX", "XX....XX", "XX....XX",
          ".XXXXXX.", "........"],
    "I": ["...XX...", "...XX...", "...XX...", "...XX...", "...XX...",
          "...XX...", "...XX...", "...XX...", "...XX...", "...XX...",
          "...XX...", "...XX..."],
    " ": ["........"] * 12,
}


def draw_text(text, cx, y, scale, color, shadow=True):
    """Draw centred text with 2x-supersampled smooth edges."""
    gw, gh = 8, 12
    gap = 2 * scale
    total_w = len(text) * gw * scale + (len(text) - 1) * gap
    x0 = cx - total_w // 2
    # supersampled mask (4x) — smooths the blocky glyph staircase
    SS = 4
    mw = total_w * SS
    mh = gh * scale * SS
    mask = bytearray(mw * mh)
    for ci, ch in enumerate(text):
        g = GLYPHS[ch]
        bx = (ci * (gw * scale + gap)) * SS
        for gy in range(gh):
            row = g[gy]
            for gx in range(gw):
                if row[gx] == 'X':
                    for sy in range(scale * SS):
                        base = (gy * scale * SS + sy) * mw + bx + gx * scale * SS
                        mask[base: base + scale * SS] = b'\xff' * (scale * SS)
    # composite with soft shadow first, then the letter
    for oy in range(mh // SS):
        for ox in range(mw // SS):
            cov = 0
            for sy in range(SS):
                for sx in range(SS):
                    cov += mask[(oy * SS + sy) * mw + ox * SS + sx]
            cov //= SS * SS
            if cov == 0:
                continue
            if shadow:
                blend(x0 + ox + 3, y + oy + 4, 90, 40, 40, cov * 120 // 255)
            blend(x0 + ox, y + oy, color[0], color[1], color[2], cov)


draw_text("ONDARA", 512, 34, 15, (250, 238, 210))
# soften the letter edges into paint (kills the pixel/Lego look)
blur_region(50, 28, 966, 222, radius=7)
# tagline rules flanking the text (never through the letters)
hspan(236, 60, 140, (250, 238, 210), 200)
hspan(237, 60, 140, (250, 238, 210), 200)
hspan(236, 884, 964, (250, 238, 210), 200)
hspan(237, 884, 964, (250, 238, 210), 200)
draw_text("THE FALLS THAT RISE", 512, 214, 4, (250, 238, 210), shadow=False)
blur_region(140, 208, 885, 270, radius=3)

# ================================================================ GRAIN + FRAME
# painterly grain: smooth-noise modulation (compresses well, no white noise)
for y in range(16, H - 16):
    row = y * W * 3
    for x in range(16, W - 16):
        d = (nf(x, y) - 128) // 22  # about +/-6
        d2 = (nc(x, y) - 128) // 42
        dd = d + d2
        if dd:
            px[row + x * 3] = clamp(px[row + x * 3] + dd)
            px[row + x * 3 + 1] = clamp(px[row + x * 3 + 1] + dd)
            px[row + x * 3 + 2] = clamp(px[row + x * 3 + 2] + dd)
# sparse halftone dots for retro print feel
for _ in range(2600):
    x = rng.randint(20, W - 21)
    y = rng.randint(20, H - 21)
    blend(x, y, 60, 40, 40, rng.randint(18, 55))
# gentle vignette
for y in range(H):
    dy = (y - H / 2) / (H / 2)
    for x in range(W):
        dx = (x - W / 2) / (W / 2)
        t = dx * dx + dy * dy
        if t > 1.15:
            a = int(min(70, (t - 1.15) * 130))
            blend(x, y, 40, 24, 24, a)

# cream frame border + inner rule
for y in range(H):
    for x in range(16):
        setpx(x, y, 244, 232, 204)
        setpx(W - 1 - x, y, 244, 232, 204)
for x in range(W):
    for y in range(16):
        setpx(x, y, 244, 232, 204)
        setpx(x, H - 1 - y, 244, 232, 204)
# frame texture
for _ in range(900):
    x = rng.randint(0, W - 1)
    y = rng.choice([rng.randint(0, 15), rng.randint(H - 16, H - 1),
                    rng.randint(0, H - 1)])
    if y < 16 or y >= H - 16:
        blend(x, y, 200, 180, 140, 60)
    else:
        blend(x, y, 200, 180, 140, 60)
# inner dark rule
for x in range(16, W - 16):
    blend(x, 18, 120, 90, 60, 140)
    blend(x, H - 19, 120, 90, 60, 140)
for y in range(16, H - 16):
    blend(18, y, 120, 90, 60, 140)
    blend(W - 19, y, 120, 90, 60, 140)

# ================================================================ PNG WRITER
def write_png(path):
    raw = bytearray()
    # build filtered rows: filter 2 (Up) everywhere except first row (None)
    prev = bytes(W * 3)
    for y in range(H):
        row = bytes(px[y * W * 3:(y + 1) * W * 3])
        if y == 0:
            raw.append(0)
            raw += row
        else:
            raw.append(2)
            raw += bytes((a - b) & 0xFF for a, b in zip(row, prev))
        prev = row
    comp = zlib.compress(bytes(raw), 9)

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", comp)
    png += chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


if __name__ == "__main__":
    out = "/output/entry.png"
    n = write_png(out)
    print(f"wrote {out}: {n} bytes")
    try:
        n2 = write_png("entry.png")
        print(f"wrote entry.png: {n2} bytes")
    except OSError as e:
        print(f"local copy skipped: {e}")
