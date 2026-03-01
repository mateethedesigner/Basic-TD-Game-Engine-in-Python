# main.py
# TD Engine (Python) + ImGui editor
# Includes:
# - A* pathfinding + computed path overlay
# - Towers (range circle), upgrades/sell, target modes
# - Wave editor (enemy types), economy + lives
# - Time controls (pause/play/step, time scale)
# - Logger + in-engine Console (F5), filters, history, save_log
# - Save/Load JSON map
# - Placement preview + ghost tower (range + valid/invalid)
# - NEW: Sprite/Texture renderer (PNG assets + atlas UV, with fallback generated textures)
# - NEW: Path Tool (waypoints -> auto-carve PATH via A* over EMPTY/PATH), preview + apply

import time
import math
import heapq
import sys
import traceback
import json
import os
from dataclasses import dataclass, asdict
from typing import List, Tuple, Optional, Dict, Any

import glfw
from OpenGL.GL import *

import imgui
from imgui.integrations.glfw import GlfwRenderer

# Optional deps for PNG loading (recommended)
# pip install pillow numpy
try:
    from PIL import Image
except Exception:
    Image = None

try:
    import numpy as np
except Exception:
    np = None

# ---------------- CONFIG ----------------
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720

GRID_WIDTH = 20
GRID_HEIGHT = 12
TILE_SIZE = 48

ASSETS_DIR = "assets"
TILESET_PATH = os.path.join(ASSETS_DIR, "tiles.png")   # 1xN tiles horizontally
ENEMY_SPRITE_PATH = os.path.join(ASSETS_DIR, "enemy.png")
TOWER_SPRITE_PATH = os.path.join(ASSETS_DIR, "tower.png")

# tile types
EMPTY = 0
WALL = 1
PATH = 2
TOWER_TILE = 3
START = 4
END = 5

COLORS = {
    EMPTY: (0.15, 0.15, 0.18),
    WALL:  (0.35, 0.35, 0.35),
    PATH:  (0.80, 0.70, 0.30),
    TOWER_TILE: (0.30, 0.80, 0.30),
    START: (0.20, 0.65, 1.00),
    END:   (1.00, 0.25, 0.35),
}

PATH_HIGHLIGHT = (0.35, 0.55, 1.00)      # computed path overlay
ENEMY_COLOR = (1.00, 0.25, 0.25)
PROJECTILE_COLOR = (1.00, 1.00, 1.00)
RANGE_COLOR = (0.40, 0.80, 1.00)
INVALID_OVERLAY = (1.00, 0.25, 0.25)

# placement preview
PREVIEW_OK = (0.25, 1.00, 0.35)
PREVIEW_BAD = (1.00, 0.25, 0.25)

# ---------------- LOGGING ----------------
LOG_DEBUG = "DEBUG"
LOG_INFO = "INFO"
LOG_WARN = "WARN"
LOG_ERROR = "ERROR"


@dataclass
class LogEntry:
    level: str
    text: str


console_open = False
console_input = ""
console_log: List[LogEntry] = []
log_filter_debug = True
log_filter_info = True
log_filter_warn = True
log_filter_error = True


def _filter_allows(level: str) -> bool:
    if level == LOG_DEBUG:
        return log_filter_debug
    if level == LOG_INFO:
        return log_filter_info
    if level == LOG_WARN:
        return log_filter_warn
    if level == LOG_ERROR:
        return log_filter_error
    return True


def log(level: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    console_log.append(LogEntry(level, line))
    if len(console_log) > 900:
        del console_log[:300]


def log_debug(msg: str): log(LOG_DEBUG, msg)
def log_info(msg: str):  log(LOG_INFO, msg)
def log_warn(msg: str):  log(LOG_WARN, msg)
def log_error(msg: str): log(LOG_ERROR, msg)


def exception_hook(exc_type, exc_value, exc_tb):
    lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    log_error("Unhandled exception:")
    for l in lines:
        for line in l.rstrip().split("\n"):
            log_error(line)


sys.excepthook = exception_hook

# ---------------- GAME STATE ----------------
grid = [[EMPTY for _ in range(GRID_WIDTH)] for _ in range(GRID_HEIGHT)]
current_brush = WALL

start_cell: Optional[Tuple[int, int]] = None
end_cell: Optional[Tuple[int, int]] = None
computed_path: List[Tuple[int, int]] = []

invalid_flash_t = 0.0
last_invalid_cell: Optional[Tuple[int, int]] = None

# economy / lives
money = 250
lives = 20

# windows toggles
show_brush_window = True
show_gameplay_window = True
show_debug_window = True
show_wave_window = True

# ---------------- RENDER MODES ----------------
use_textures = True
show_grid_lines = True

# ---------------- TIME CONTROLS ----------------
paused = False
time_scale = 1.0
_step_once = False  # one fixed tick while paused


def fixed_step_dt() -> float:
    return 1.0 / 60.0


def compute_game_dt(real_dt: float) -> float:
    global _step_once
    real_dt = min(real_dt, 0.1)
    if paused:
        if _step_once:
            _step_once = False
            return fixed_step_dt()
        return 0.0
    return real_dt * max(0.0, float(time_scale))


# ---------------- EDITOR MODES ----------------
EDIT_PAINT = "Paint"
EDIT_PATH_TOOL = "Path Tool"
editor_mode = EDIT_PAINT

# Path tool state
path_waypoints: List[Tuple[int, int]] = []
path_preview_cells: List[Tuple[int, int]] = []
path_tool_clear_existing_path = True

# ---------------- TOWER / ENEMY DEFINITIONS ----------------
TARGET_MODES = ["First", "Last", "Closest", "Strongest"]


def tower_stats(level: int) -> Dict[str, float]:
    lvl = max(1, int(level))
    base_range = 3.5
    base_dmg = 14
    base_rate = 2.0  # shots/s
    return {
        "range": base_range + 0.35 * (lvl - 1),
        "damage": base_dmg + 6 * (lvl - 1),
        "rate": base_rate + 0.25 * (lvl - 1),
    }


def tower_cost(level_to_build: int) -> int:
    return 50


def tower_upgrade_cost(current_level: int) -> int:
    return 45 + (current_level * 35)


@dataclass
class EnemyDef:
    name: str
    hp: int
    speed: float
    reward: int


ENEMY_DEFS: Dict[str, EnemyDef] = {
    "normal": EnemyDef("normal", hp=60, speed=4.0, reward=8),
    "fast":   EnemyDef("fast",   hp=35, speed=6.5, reward=7),
    "tank":   EnemyDef("tank",   hp=140, speed=2.8, reward=14),
}
enemy_def_names = list(ENEMY_DEFS.keys())

# ---------------- WAVE EDITOR ----------------
@dataclass
class WaveEntry:
    enemy_type: str = "normal"
    count: int = 10
    interval: float = 0.60


wave_enabled = True
wave_plan: List[WaveEntry] = [
    WaveEntry("normal", 10, 0.60),
    WaveEntry("fast", 8, 0.45),
    WaveEntry("tank", 6, 0.85),
]

# runtime wave state
wave_running = False
wave_entry_index = 0
wave_spawn_remaining = 0
wave_spawn_timer = 0.0

# ---------------- TEXTURE SYSTEM ----------------
def _gl_create_texture_from_rgba_bytes(w: int, h: int, rgba_bytes: bytes) -> int:
    tex = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, tex)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, rgba_bytes)
    glBindTexture(GL_TEXTURE_2D, 0)
    return tex


def _load_png_rgba(path: str) -> Optional[Tuple[int, int, bytes]]:
    if Image is None:
        return None
    if not os.path.exists(path):
        return None
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    return w, h, im.tobytes("raw", "RGBA", 0, -1)


def _make_placeholder_tileset_rgba(tile_w: int, tile_h: int, tile_count: int) -> Tuple[int, int, bytes]:
    # tiles arranged 1xN horizontally
    w = tile_w * tile_count
    h = tile_h
    if np is None:
        # pure python fallback (slow but fine for small)
        data = bytearray(w * h * 4)

        def put_px(x, y, r, g, b, a=255):
            i = (y * w + x) * 4
            data[i + 0] = r
            data[i + 1] = g
            data[i + 2] = b
            data[i + 3] = a

        # use COLORS as base
        for ti in range(tile_count):
            col = COLORS.get(ti, (0.8, 0.2, 0.8))
            r = int(col[0] * 255)
            g = int(col[1] * 255)
            b = int(col[2] * 255)
            ox = ti * tile_w
            for y in range(tile_h):
                for x in range(tile_w):
                    # subtle border
                    border = (x == 0 or y == 0 or x == tile_w - 1 or y == tile_h - 1)
                    if border:
                        put_px(ox + x, y, 0, 0, 0, 255)
                    else:
                        put_px(ox + x, y, r, g, b, 255)
        return w, h, bytes(data)

    arr = np.zeros((h, w, 4), dtype=np.uint8)
    for ti in range(tile_count):
        col = COLORS.get(ti, (0.8, 0.2, 0.8))
        rgb = (int(col[0] * 255), int(col[1] * 255), int(col[2] * 255))
        ox = ti * tile_w
        arr[:, ox:ox + tile_w, 0] = rgb[0]
        arr[:, ox:ox + tile_w, 1] = rgb[1]
        arr[:, ox:ox + tile_w, 2] = rgb[2]
        arr[:, ox:ox + tile_w, 3] = 255
        # border
        arr[:, ox:ox + tile_w, :3] = arr[:, ox:ox + tile_w, :3]
        arr[0, ox:ox + tile_w, :3] = 0
        arr[-1, ox:ox + tile_w, :3] = 0
        arr[:, ox, :3] = 0
        arr[:, ox + tile_w - 1, :3] = 0
    return w, h, arr.tobytes()


def _make_placeholder_sprite_rgba(w: int, h: int, color: Tuple[float, float, float]) -> Tuple[int, int, bytes]:
    if np is None:
        data = bytearray(w * h * 4)
        r = int(color[0] * 255)
        g = int(color[1] * 255)
        b = int(color[2] * 255)
        for y in range(h):
            for x in range(w):
                i = (y * w + x) * 4
                border = (x == 0 or y == 0 or x == w - 1 or y == h - 1)
                data[i + 0] = 0 if border else r
                data[i + 1] = 0 if border else g
                data[i + 2] = 0 if border else b
                data[i + 3] = 255
        return w, h, bytes(data)

    arr = np.zeros((h, w, 4), dtype=np.uint8)
    rgb = (int(color[0] * 255), int(color[1] * 255), int(color[2] * 255))
    arr[:, :, 0] = rgb[0]
    arr[:, :, 1] = rgb[1]
    arr[:, :, 2] = rgb[2]
    arr[:, :, 3] = 255
    arr[0, :, :3] = 0
    arr[-1, :, :3] = 0
    arr[:, 0, :3] = 0
    arr[:, -1, :3] = 0
    return w, h, arr.tobytes()


@dataclass
class Texture:
    tex_id: int
    width: int
    height: int


class TextureManager:
    def __init__(self):
        self.tileset: Optional[Texture] = None
        self.enemy: Optional[Texture] = None
        self.tower: Optional[Texture] = None
        self.tileset_count = 6  # EMPTY..END
        self.tileset_tile_w = 16
        self.tileset_tile_h = 16

    def load_or_create(self):
        # Tileset
        tiles = _load_png_rgba(TILESET_PATH)
        if tiles is not None:
            w, h, data = tiles
            tex_id = _gl_create_texture_from_rgba_bytes(w, h, data)
            self.tileset = Texture(tex_id, w, h)
            # infer tile size if 1xN strip
            self.tileset_tile_h = h
            self.tileset_tile_w = max(1, w // self.tileset_count)
            log_info(f"Loaded tileset: {TILESET_PATH} ({w}x{h}), tile {self.tileset_tile_w}x{self.tileset_tile_h}")
        else:
            w, h, data = _make_placeholder_tileset_rgba(self.tileset_tile_w, self.tileset_tile_h, self.tileset_count)
            tex_id = _gl_create_texture_from_rgba_bytes(w, h, data)
            self.tileset = Texture(tex_id, w, h)
            if Image is None:
                log_warn("Pillow not installed -> using generated placeholder tileset.")
            else:
                log_warn(f"Tileset not found at {TILESET_PATH} -> using generated placeholder tileset.")
            log_info("Tip: put your own tileset at assets/tiles.png (1 row, 6 tiles).")

        # Enemy sprite
        enemy = _load_png_rgba(ENEMY_SPRITE_PATH)
        if enemy is not None:
            w, h, data = enemy
            self.enemy = Texture(_gl_create_texture_from_rgba_bytes(w, h, data), w, h)
            log_info(f"Loaded enemy sprite: {ENEMY_SPRITE_PATH} ({w}x{h})")
        else:
            w, h, data = _make_placeholder_sprite_rgba(16, 16, ENEMY_COLOR)
            self.enemy = Texture(_gl_create_texture_from_rgba_bytes(w, h, data), w, h)
            log_warn(f"Enemy sprite not found at {ENEMY_SPRITE_PATH} -> using placeholder.")

        # Tower sprite
        tower = _load_png_rgba(TOWER_SPRITE_PATH)
        if tower is not None:
            w, h, data = tower
            self.tower = Texture(_gl_create_texture_from_rgba_bytes(w, h, data), w, h)
            log_info(f"Loaded tower sprite: {TOWER_SPRITE_PATH} ({w}x{h})")
        else:
            w, h, data = _make_placeholder_sprite_rgba(16, 16, COLORS[TOWER_TILE])
            self.tower = Texture(_gl_create_texture_from_rgba_bytes(w, h, data), w, h)
            log_warn(f"Tower sprite not found at {TOWER_SPRITE_PATH} -> using placeholder.")

    def bind(self, tex: Texture):
        glBindTexture(GL_TEXTURE_2D, tex.tex_id)

    def unbind(self):
        glBindTexture(GL_TEXTURE_2D, 0)

    def tile_uv(self, tile_index: int) -> Tuple[float, float, float, float]:
        # assumes tileset is 1xN strip
        n = self.tileset_count
        i = max(0, min(n - 1, int(tile_index)))
        u0 = i / n
        u1 = (i + 1) / n
        v0 = 0.0
        v1 = 1.0
        return u0, v0, u1, v1


texman = TextureManager()

# ---------------- CLASSES ----------------
class Enemy:
    def __init__(self, path: List[Tuple[int, int]], etype: str):
        self.active = True
        self.path_index = 0
        self.x = float(path[0][0])
        self.y = float(path[0][1])

        self.etype = etype if etype in ENEMY_DEFS else "normal"
        ed = ENEMY_DEFS[self.etype]
        self.speed = float(ed.speed)
        self.max_hp = int(ed.hp)
        self.hp = int(ed.hp)
        self.reward = int(ed.reward)

    def progress(self) -> float:
        return float(self.path_index) + 0.01 * (self.x + self.y)

    def update(self, dt: float, path: List[Tuple[int, int]]):
        global lives
        if not self.active or not path:
            return
        if self.path_index >= len(path) - 1:
            self.active = False
            lives = max(0, lives - 1)
            log_warn(f"Enemy reached end. Lives: {lives}")
            return

        tx, ty = path[self.path_index + 1]
        dx = tx - self.x
        dy = ty - self.y
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            self.path_index += 1
            return

        step = self.speed * dt
        if step >= dist:
            self.x, self.y = float(tx), float(ty)
            self.path_index += 1
        else:
            self.x += dx / dist * step
            self.y += dy / dist * step


class Tower:
    def __init__(self, cell: Tuple[int, int], level: int = 1, target_mode: str = "First"):
        self.cx, self.cy = cell
        self.level = max(1, int(level))
        self.cooldown = 0.0
        self.target_mode = target_mode if target_mode in TARGET_MODES else "First"

    def stats(self) -> Dict[str, float]:
        return tower_stats(self.level)

    def pick_target(self, enemies: List["Enemy"], rng: float) -> Optional["Enemy"]:
        cand = []
        for e in enemies:
            if not e.active:
                continue
            dx = (e.x - self.cx)
            dy = (e.y - self.cy)
            d = math.hypot(dx, dy)
            if d <= rng:
                cand.append((e, d))
        if not cand:
            return None

        mode = self.target_mode
        if mode == "Closest":
            cand.sort(key=lambda t: t[1])
            return cand[0][0]
        if mode == "Strongest":
            cand.sort(key=lambda t: t[0].hp, reverse=True)
            return cand[0][0]
        if mode == "Last":
            cand.sort(key=lambda t: t[0].progress())
            return cand[0][0]
        cand.sort(key=lambda t: t[0].progress(), reverse=True)  # First
        return cand[0][0]

    def update(self, dt: float, enemies: List["Enemy"], projectiles: List["Projectile"]):
        s = self.stats()
        fire_rate = float(s["rate"])
        rng = float(s["range"])
        dmg = int(s["damage"])

        if self.cooldown > 0:
            self.cooldown = max(0.0, self.cooldown - dt)
        if self.cooldown > 0:
            return

        target = self.pick_target(enemies, rng)
        if target is None:
            return

        self.cooldown = 1.0 / max(0.001, fire_rate)
        projectiles.append(Projectile(self.cx + 0.5, self.cy + 0.5, target, dmg))


class Projectile:
    def __init__(self, sx: float, sy: float, target: Enemy, dmg: int):
        self.x = sx
        self.y = sy
        self.target = target
        self.dmg = int(dmg)
        self.speed = 14.0
        self.active = True

    def update(self, dt: float):
        global money
        if not self.active:
            return
        if self.target is None or not self.target.active:
            self.active = False
            return

        tx = self.target.x + 0.5
        ty = self.target.y + 0.5
        dx = tx - self.x
        dy = ty - self.y
        dist = math.hypot(dx, dy)

        if dist < 0.15:
            self.target.hp -= self.dmg
            if self.target.hp <= 0:
                self.target.active = False
                money += self.target.reward
                log_info(f"Enemy killed (+{self.target.reward}$). Money: {money}")
            self.active = False
            return

        step = self.speed * dt
        if step >= dist:
            self.x, self.y = tx, ty
        else:
            self.x += dx / dist * step
            self.y += dy / dist * step


towers: Dict[Tuple[int, int], Tower] = {}
enemies: List[Enemy] = []
projectiles: List[Projectile] = []

# ---------------- RENDER HELPERS ----------------
def draw_rect(x, y, w, h, color):
    glColor3f(*color)
    glBegin(GL_QUADS)
    glVertex2f(x, y)
    glVertex2f(x + w, y)
    glVertex2f(x + w, y + h)
    glVertex2f(x, y + h)
    glEnd()


def draw_rect_alpha(x, y, w, h, color, alpha=0.35):
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glColor4f(color[0], color[1], color[2], alpha)
    glBegin(GL_QUADS)
    glVertex2f(x, y)
    glVertex2f(x + w, y)
    glVertex2f(x + w, y + h)
    glVertex2f(x, y + h)
    glEnd()
    glDisable(GL_BLEND)


def draw_circle(cx, cy, radius, color, segments=72):
    glColor3f(*color)
    glBegin(GL_LINE_LOOP)
    for i in range(segments):
        a = 2.0 * math.pi * i / segments
        glVertex2f(cx + math.cos(a) * radius, cy + math.sin(a) * radius)
    glEnd()


def draw_filled_circle(cx, cy, radius, color, alpha=0.16, segments=72):
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glColor4f(color[0], color[1], color[2], alpha)

    glBegin(GL_TRIANGLE_FAN)
    glVertex2f(cx, cy)
    for i in range(segments + 1):
        a = 2.0 * math.pi * i / segments
        glVertex2f(cx + math.cos(a) * radius, cy + math.sin(a) * radius)
    glEnd()

    glDisable(GL_BLEND)


def draw_grid_lines():
    if not show_grid_lines:
        return
    glColor3f(0.08, 0.08, 0.09)
    glBegin(GL_LINES)
    # vertical
    for x in range(GRID_WIDTH + 1):
        px = x * TILE_SIZE
        glVertex2f(px, 0)
        glVertex2f(px, GRID_HEIGHT * TILE_SIZE)
    # horizontal
    for y in range(GRID_HEIGHT + 1):
        py = y * TILE_SIZE
        glVertex2f(0, py)
        glVertex2f(GRID_WIDTH * TILE_SIZE, py)
    glEnd()


def draw_textured_quad(x, y, w, h, u0, v0, u1, v1):
    glBegin(GL_QUADS)
    glTexCoord2f(u0, v0); glVertex2f(x, y)
    glTexCoord2f(u1, v0); glVertex2f(x + w, y)
    glTexCoord2f(u1, v1); glVertex2f(x + w, y + h)
    glTexCoord2f(u0, v1); glVertex2f(x, y + h)
    glEnd()


def screen_to_grid(mx, my):
    gx = int(mx // TILE_SIZE)
    gy = int(my // TILE_SIZE)
    if 0 <= gx < GRID_WIDTH and 0 <= gy < GRID_HEIGHT:
        return gx, gy
    return None


def tile_center_px(cell: Tuple[int, int]):
    x, y = cell
    return x * TILE_SIZE + TILE_SIZE * 0.5, y * TILE_SIZE + TILE_SIZE * 0.5


# ---------------- PATHFINDING ----------------
def is_walkable_game(x, y):
    t = grid[y][x]
    return t in (PATH, START, END)


def is_walkable_tool(x, y):
    # Path tool can walk through EMPTY or PATH or start/end, but not WALL nor TOWER
    t = grid[y][x]
    return t in (EMPTY, PATH, START, END)


def heuristic(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def a_star_custom(start: Tuple[int, int], goal: Tuple[int, int], walkable_fn) -> List[Tuple[int, int]]:
    if start is None or goal is None:
        return []
    if not walkable_fn(*start) or not walkable_fn(*goal):
        return []

    open_heap = []
    heapq.heappush(open_heap, (0, start))
    came_from = {}
    g_score = {start: 0}
    in_open = {start}

    while open_heap:
        _, current = heapq.heappop(open_heap)
        in_open.discard(current)

        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        cx, cy = current
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if not (0 <= nx < GRID_WIDTH and 0 <= ny < GRID_HEIGHT):
                continue
            if not walkable_fn(nx, ny):
                continue
            tentative = g_score[current] + 1
            n = (nx, ny)
            if tentative < g_score.get(n, 1_000_000_000):
                came_from[n] = current
                g_score[n] = tentative
                f = tentative + heuristic(n, goal)
                if n not in in_open:
                    heapq.heappush(open_heap, (f, n))
                    in_open.add(n)

    return []


def a_star_game(start: Tuple[int, int], goal: Tuple[int, int]) -> List[Tuple[int, int]]:
    return a_star_custom(start, goal, is_walkable_game)


def rebuild_path():
    global computed_path
    if start_cell is None or end_cell is None:
        computed_path = []
        return
    computed_path = a_star_game(start_cell, end_cell)


# ---------------- EDITOR ACTIONS ----------------
def set_unique_cell(kind, cell):
    global start_cell, end_cell
    if kind == START and start_cell is not None:
        px, py = start_cell
        if grid[py][px] == START:
            grid[py][px] = PATH
    if kind == END and end_cell is not None:
        px, py = end_cell
        if grid[py][px] == END:
            grid[py][px] = PATH

    x, y = cell
    grid[y][x] = kind
    if kind == START:
        start_cell = cell
    else:
        end_cell = cell


def can_place_blocking(cell, kind):
    if cell == start_cell or cell == end_cell:
        return False
    x, y = cell
    old = grid[y][x]
    grid[y][x] = kind
    ok = True
    if start_cell and end_cell:
        ok = bool(a_star_game(start_cell, end_cell))
    grid[y][x] = old
    return ok


def remove_tower_if_exists(cell):
    if cell in towers:
        del towers[cell]


def tower_place_valid(cell) -> Tuple[bool, str]:
    if cell is None:
        return False, "no cell"
    if cell == start_cell or cell == end_cell:
        return False, "start/end"
    x, y = cell
    if grid[y][x] != EMPTY and grid[y][x] != TOWER_TILE:
        return False, "not empty"
    if cell not in towers and money < tower_cost(1):
        return False, "no money"
    if start_cell and end_cell:
        if not can_place_blocking(cell, TOWER_TILE):
            return False, "blocks path"
    return True, "ok"


def try_place_tower(cell) -> bool:
    global money, invalid_flash_t, last_invalid_cell
    ok, _ = tower_place_valid(cell)
    if not ok:
        invalid_flash_t = 0.25
        last_invalid_cell = cell
        log_warn("Cannot place tower here.")
        return False

    cost = tower_cost(1)
    if cell not in towers:
        money -= cost
        log_info(f"Placed tower (-{cost}$). Money: {money}")

    grid[cell[1]][cell[0]] = TOWER_TILE
    towers[cell] = Tower(
        cell,
        level=towers[cell].level if cell in towers else 1,
        target_mode=towers[cell].target_mode if cell in towers else "First"
    )
    rebuild_path()
    return True


def place_tile(cell, kind):
    global invalid_flash_t, last_invalid_cell

    x, y = cell

    if kind == START:
        grid[y][x] = PATH
        set_unique_cell(START, cell)
        rebuild_path()
        return

    if kind == END:
        grid[y][x] = PATH
        set_unique_cell(END, cell)
        rebuild_path()
        return

    if kind == TOWER_TILE:
        try_place_tower(cell)
        return

    if kind == EMPTY:
        if cell == start_cell or cell == end_cell:
            return
        grid[y][x] = EMPTY
        remove_tower_if_exists(cell)
        rebuild_path()
        return

    if kind == PATH:
        if cell == start_cell or cell == end_cell:
            return
        grid[y][x] = PATH
        remove_tower_if_exists(cell)
        rebuild_path()
        return

    if kind == WALL:
        if start_cell and end_cell and not can_place_blocking(cell, WALL):
            invalid_flash_t = 0.25
            last_invalid_cell = cell
            log_warn("Invalid placement: would block the path.")
            return
        if cell == start_cell or cell == end_cell:
            invalid_flash_t = 0.25
            last_invalid_cell = cell
            log_warn("Cannot place wall on Start/End.")
            return
        grid[y][x] = WALL
        remove_tower_if_exists(cell)
        rebuild_path()
        return


# ---------------- PATH TOOL ----------------
def path_tool_clear():
    path_waypoints.clear()
    path_preview_cells.clear()
    log_info("Path tool cleared.")


def _collect_preview_from_waypoints() -> Tuple[bool, str, List[Tuple[int, int]]]:
    if len(path_waypoints) < 2:
        return False, "Need at least 2 waypoints.", []
    preview: List[Tuple[int, int]] = []
    for i in range(len(path_waypoints) - 1):
        a = path_waypoints[i]
        b = path_waypoints[i + 1]
        seg = a_star_custom(a, b, is_walkable_tool)
        if not seg:
            return False, f"No route between {a} and {b}.", []
        if preview:
            preview.extend(seg[1:])  # avoid duplicate node
        else:
            preview.extend(seg)
    return True, "ok", preview


def path_tool_rebuild_preview():
    ok, msg, preview = _collect_preview_from_waypoints()
    path_preview_cells.clear()
    if not ok:
        if len(path_waypoints) >= 2:
            log_warn("Path preview: " + msg)
        return
    path_preview_cells.extend(preview)


def path_tool_apply():
    global start_cell, end_cell
    ok, msg, preview = _collect_preview_from_waypoints()
    if not ok:
        log_warn("Path apply failed: " + msg)
        return

    first = path_waypoints[0]
    last = path_waypoints[-1]

    # Optionally clear existing PATH tiles (keep walls/towers)
    if path_tool_clear_existing_path:
        for y in range(GRID_HEIGHT):
            for x in range(GRID_WIDTH):
                if grid[y][x] == PATH:
                    grid[y][x] = EMPTY

    # Set Start/End from tool
    # Ensure those cells become PATH first (so unique setter doesn't leave holes)
    fx, fy = first
    lx, ly = last

    # If there is a tower/wall at start/end, remove/deny
    if grid[fy][fx] == WALL or grid[fy][fx] == TOWER_TILE:
        log_warn("Start waypoint is blocked by WALL/TOWER. Move it.")
        return
    if grid[ly][lx] == WALL or grid[ly][lx] == TOWER_TILE:
        log_warn("End waypoint is blocked by WALL/TOWER. Move it.")
        return

    grid[fy][fx] = PATH
    grid[ly][lx] = PATH
    set_unique_cell(START, first)
    set_unique_cell(END, last)

    # Carve path cells
    for (px, py) in preview:
        if (px, py) == start_cell or (px, py) == end_cell:
            continue
        if grid[py][px] == WALL or grid[py][px] == TOWER_TILE:
            # shouldn't happen since tool walkable excludes these
            continue
        grid[py][px] = PATH

    rebuild_path()
    path_preview_cells.clear()
    log_info(f"Path tool applied. Waypoints: {len(path_waypoints)}  Cells: {len(preview)}")


# ---------------- WAVE RUNTIME ----------------
def wave_stop():
    global wave_running, wave_entry_index, wave_spawn_remaining, wave_spawn_timer
    wave_running = False
    wave_entry_index = 0
    wave_spawn_remaining = 0
    wave_spawn_timer = 0.0
    log_info("Wave stopped.")


def wave_start():
    global wave_running, wave_entry_index, wave_spawn_remaining, wave_spawn_timer
    if not computed_path:
        log_warn("Cannot start wave: no path.")
        return
    if not wave_plan:
        log_warn("Cannot start wave: wave plan is empty.")
        return
    wave_running = True
    wave_entry_index = 0
    wave_spawn_remaining = max(0, int(wave_plan[0].count))
    wave_spawn_timer = 0.0
    log_info("Wave started.")


def spawn_enemy(etype: str = "normal"):
    if not computed_path:
        log_warn("Cannot spawn: no path.")
        return
    enemies.append(Enemy(computed_path, etype))


def update_wave(dt: float):
    global wave_running, wave_entry_index, wave_spawn_remaining, wave_spawn_timer
    if not wave_enabled or not wave_running:
        return
    if not computed_path:
        wave_stop()
        return
    if wave_entry_index >= len(wave_plan):
        wave_running = False
        log_info("Wave finished.")
        return

    entry = wave_plan[wave_entry_index]
    wave_spawn_timer -= dt
    if wave_spawn_remaining > 0 and wave_spawn_timer <= 0.0:
        spawn_enemy(entry.enemy_type)
        wave_spawn_remaining -= 1
        wave_spawn_timer = max(0.02, float(entry.interval))

    if wave_spawn_remaining <= 0:
        wave_entry_index += 1
        if wave_entry_index < len(wave_plan):
            nxt = wave_plan[wave_entry_index]
            wave_spawn_remaining = max(0, int(nxt.count))
            wave_spawn_timer = 0.2
        else:
            wave_running = False
            log_info("Wave finished.")


# ---------------- SAVE / LOAD ----------------
def serialize_state() -> Dict[str, Any]:
    return {
        "version": 3,
        "grid_w": GRID_WIDTH,
        "grid_h": GRID_HEIGHT,
        "grid": grid,
        "start": list(start_cell) if start_cell else None,
        "end": list(end_cell) if end_cell else None,
        "towers": [{"x": k[0], "y": k[1], "level": t.level, "target_mode": t.target_mode} for k, t in towers.items()],
        "money": money,
        "lives": lives,
        "wave_enabled": wave_enabled,
        "wave_plan": [asdict(w) for w in wave_plan],
        "time": {"paused": paused, "time_scale": time_scale},
        "render": {"use_textures": use_textures, "show_grid_lines": show_grid_lines},
    }


def apply_state(data: Dict[str, Any]):
    global grid, start_cell, end_cell, towers, enemies, projectiles
    global money, lives, wave_enabled, wave_plan
    global wave_running, wave_entry_index, wave_spawn_remaining, wave_spawn_timer
    global paused, time_scale
    global use_textures, show_grid_lines

    g = data.get("grid")
    if not isinstance(g, list):
        raise ValueError("Invalid file: grid missing")

    new_grid = [[EMPTY for _ in range(GRID_WIDTH)] for _ in range(GRID_HEIGHT)]
    for y in range(min(GRID_HEIGHT, len(g))):
        row = g[y]
        if not isinstance(row, list):
            continue
        for x in range(min(GRID_WIDTH, len(row))):
            v = int(row[x])
            if v not in (EMPTY, WALL, PATH, TOWER_TILE, START, END):
                v = EMPTY
            new_grid[y][x] = v
    grid = new_grid

    s = data.get("start")
    e = data.get("end")
    start_cell = (int(s[0]), int(s[1])) if isinstance(s, list) and len(s) == 2 else None
    end_cell = (int(e[0]), int(e[1])) if isinstance(e, list) and len(e) == 2 else None

    towers = {}
    tlist = data.get("towers", [])
    if isinstance(tlist, list):
        for it in tlist:
            try:
                x = int(it["x"])
                y = int(it["y"])
                lvl = int(it.get("level", 1))
                mode = str(it.get("target_mode", "First"))
                if mode not in TARGET_MODES:
                    mode = "First"
                if 0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT:
                    towers[(x, y)] = Tower((x, y), lvl, mode)
                    grid[y][x] = TOWER_TILE
            except Exception:
                continue

    money = int(data.get("money", 250))
    lives = int(data.get("lives", 20))

    wave_enabled = bool(data.get("wave_enabled", True))
    wp = data.get("wave_plan", [])
    wave_plan = []
    if isinstance(wp, list):
        for w in wp:
            try:
                et = str(w.get("enemy_type", "normal")).lower()
                if et not in ENEMY_DEFS:
                    et = "normal"
                cnt = int(w.get("count", 10))
                inter = float(w.get("interval", 0.6))
                wave_plan.append(WaveEntry(et, max(0, cnt), max(0.02, inter)))
            except Exception:
                continue
    if not wave_plan:
        wave_plan = [WaveEntry("normal", 10, 0.6)]

    tcfg = data.get("time", {})
    if isinstance(tcfg, dict):
        paused = bool(tcfg.get("paused", False))
        time_scale = float(tcfg.get("time_scale", 1.0))

    rcfg = data.get("render", {})
    if isinstance(rcfg, dict):
        use_textures = bool(rcfg.get("use_textures", True))
        show_grid_lines = bool(rcfg.get("show_grid_lines", True))

    enemies = []
    projectiles = []
    wave_running = False
    wave_entry_index = 0
    wave_spawn_remaining = 0
    wave_spawn_timer = 0.0

    rebuild_path()


def save_to_file(filename: str):
    try:
        data = serialize_state()
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log_info(f"Saved: {filename}")
    except Exception as ex:
        log_error(f"Save failed: {ex}")


def load_from_file(filename: str):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        apply_state(data)
        log_info(f"Loaded: {filename}")
    except Exception as ex:
        log_error(f"Load failed: {ex}")


# ---------------- CONSOLE COMMANDS ----------------
def save_log_to_file(filename: str):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            for e in console_log:
                f.write(e.text + "\n")
        log_info(f"Saved log: {filename}")
    except Exception as ex:
        log_error(f"Failed to save log: {ex}")


def apply_console_command(cmd: str):
    global show_brush_window, show_gameplay_window, show_debug_window, show_wave_window
    global log_filter_debug, log_filter_info, log_filter_warn, log_filter_error
    global money, lives, paused, time_scale, _step_once
    global editor_mode, use_textures, show_grid_lines, path_tool_clear_existing_path

    c = cmd.strip()
    if not c:
        return
    log_debug("> " + c)

    parts = c.split()
    lp = [p.lower() for p in parts]

    def set_window(name: str, value: bool):
        if name == "brush":
            show_brush_window = value
        elif name == "gameplay":
            show_gameplay_window = value
        elif name == "debug":
            show_debug_window = value
        elif name == "wave":
            show_wave_window = value
        else:
            log_warn("Unknown window: " + name)

    def toggle_window(name: str):
        if name == "brush":
            show_brush_window = not show_brush_window
        elif name == "gameplay":
            show_gameplay_window = not show_gameplay_window
        elif name == "debug":
            show_debug_window = not show_debug_window
        elif name == "wave":
            show_wave_window = not show_wave_window
        else:
            log_warn("Unknown window: " + name)

    if lp[0] == "help":
        log_info("Commands:")
        log_info("  help")
        log_info("  show|hide|toggle brush|gameplay|debug|wave")
        log_info("  wave start|stop")
        log_info("  spawn [normal|fast|tank]")
        log_info("  save file.json / load file.json")
        log_info("  save_log file.txt")
        log_info("  filter debug|info|warn|error on|off")
        log_info("  set money <n> / set lives <n>")
        log_info("  time pause|play|step|scale <n>")
        log_info("  render textures on|off")
        log_info("  render grid on|off")
        log_info("  mode paint|path")
        log_info("  path clear | path apply | path add x y | path pop | path reset")
        log_info("  path clear_existing on|off")
        log_info("  clear")
        return

    if lp[0] == "clear":
        console_log.clear()
        log_info("Console cleared.")
        return

    if lp[0] in ("show", "hide", "toggle") and len(lp) >= 2:
        target = lp[1]
        if lp[0] == "show":
            set_window(target, True)
        elif lp[0] == "hide":
            set_window(target, False)
        else:
            toggle_window(target)
        return

    if lp[0] == "wave" and len(lp) >= 2:
        if lp[1] == "start":
            wave_start()
        elif lp[1] == "stop":
            wave_stop()
        else:
            log_warn("Usage: wave start|stop")
        return

    if lp[0] == "spawn":
        et = lp[1] if len(lp) >= 2 else "normal"
        if et not in ENEMY_DEFS:
            et = "normal"
        spawn_enemy(et)
        return

    if lp[0] == "save" and len(parts) >= 2:
        save_to_file(parts[1])
        return

    if lp[0] == "load" and len(parts) >= 2:
        load_from_file(parts[1])
        return

    if lp[0] == "save_log" and len(parts) >= 2:
        save_log_to_file(parts[1])
        return

    if lp[0] == "filter" and len(lp) >= 3:
        which = lp[1]
        state = lp[2]
        on = state in ("on", "1", "true", "yes")
        if which == "debug":
            log_filter_debug = on
        elif which == "info":
            log_filter_info = on
        elif which == "warn":
            log_filter_warn = on
        elif which == "error":
            log_filter_error = on
        else:
            log_warn("Unknown filter: " + which)
            return
        log_info(f"Filter {which} set to {'ON' if on else 'OFF'}.")
        return

    if lp[0] == "set" and len(lp) >= 3:
        key = lp[1]
        try:
            val = int(lp[2])
            if key == "money":
                money = max(0, val)
                log_info(f"Money set to {money}.")
            elif key == "lives":
                lives = max(0, val)
                log_info(f"Lives set to {lives}.")
            else:
                log_warn("Unknown set key. Use money|lives")
        except Exception:
            log_warn("Usage: set money <n> / set lives <n>")
        return

    if lp[0] == "time" and len(lp) >= 2:
        sub = lp[1]
        if sub == "pause":
            paused = True
            log_info("Paused.")
        elif sub == "play":
            paused = False
            log_info("Playing.")
        elif sub == "step":
            paused = True
            _step_once = True
            log_info("Step.")
        elif sub == "scale" and len(lp) >= 3:
            try:
                time_scale = float(lp[2])
                log_info(f"Time scale set to {time_scale}.")
            except Exception:
                log_warn("Usage: time scale <number>")
        else:
            log_warn("Usage: time pause|play|step|scale <n>")
        return

    if lp[0] == "render" and len(lp) >= 3:
        what = lp[1]
        on = lp[2] in ("on", "1", "true", "yes")
        if what == "textures":
            use_textures = on
            log_info(f"Textures {'ON' if on else 'OFF'}.")
        elif what == "grid":
            show_grid_lines = on
            log_info(f"Grid lines {'ON' if on else 'OFF'}.")
        else:
            log_warn("Usage: render textures|grid on|off")
        return

    if lp[0] == "mode" and len(lp) >= 2:
        m = lp[1]
        if m in ("paint", "p"):
            editor_mode = EDIT_PAINT
            log_info("Editor mode: Paint")
        elif m in ("path", "pathtool"):
            editor_mode = EDIT_PATH_TOOL
            log_info("Editor mode: Path Tool")
        else:
            log_warn("Usage: mode paint|path")
        return

    if lp[0] == "path":
        if len(lp) == 1:
            log_warn("Usage: path clear|apply|add x y|pop|reset|clear_existing on|off")
            return
        sub = lp[1]
        if sub in ("clear", "reset"):
            path_tool_clear()
            return
        if sub == "apply":
            path_tool_apply()
            return
        if sub == "pop":
            if path_waypoints:
                path_waypoints.pop()
                path_tool_rebuild_preview()
                log_info("Path waypoint popped.")
            return
        if sub == "add" and len(lp) >= 4:
            try:
                x = int(lp[2]); y = int(lp[3])
                if 0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT:
                    path_waypoints.append((x, y))
                    path_tool_rebuild_preview()
                    log_info(f"Path waypoint added: {(x, y)}")
                else:
                    log_warn("Waypoint out of bounds.")
            except Exception:
                log_warn("Usage: path add x y")
            return
        if sub == "clear_existing" and len(lp) >= 3:
            path_tool_clear_existing_path = lp[2] in ("on", "1", "true", "yes")
            log_info(f"Path tool clear existing PATH: {'ON' if path_tool_clear_existing_path else 'OFF'}")
            return
        log_warn("Unknown path command.")
        return

    log_warn("Unknown command. Type 'help'.")


# ---------------- MAIN ----------------
def main():
    global current_brush, invalid_flash_t
    global money, lives
    global wave_enabled, wave_plan
    global wave_running
    global console_open, console_input
    global log_filter_debug, log_filter_info, log_filter_warn, log_filter_error
    global paused, time_scale, _step_once
    global editor_mode, use_textures, show_grid_lines
    global path_tool_clear_existing_path

    # -------- GLFW --------
    if not glfw.init():
        raise RuntimeError("GLFW init failed")

    window = glfw.create_window(WINDOW_WIDTH, WINDOW_HEIGHT, "TD Engine - Textures + Path Tool + Ghost Preview", None, None)
    if not window:
        glfw.terminate()
        raise RuntimeError("GLFW create window failed")

    glfw.make_context_current(window)
    glfw.swap_interval(1)

    # -------- OpenGL 2D --------
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    glOrtho(0, WINDOW_WIDTH, WINDOW_HEIGHT, 0, -1, 1)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()
    glClearColor(0.1, 0.1, 0.12, 1)

    # Texture init (needs GL context)
    glDisable(GL_DEPTH_TEST)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glDisable(GL_TEXTURE_2D)  # enabled only when drawing textured things

    texman.load_or_create()

    # -------- ImGui --------
    imgui.create_context()
    impl = GlfwRenderer(window)

    last_time = time.time()

    # selection
    selected_tower: Optional[Tuple[int, int]] = None
    show_range = True

    # key edge detection
    f5_was_down = False
    space_was_down = False
    enter_was_down = False
    backspace_was_down = False
    esc_was_down = False

    # console history
    history: List[str] = []
    history_index = -1

    # save/load quick filenames
    save_filename = "map.json"
    load_filename = "map.json"

    # wave editor temp new entry controls
    new_enemy_idx = 0
    new_count = 10
    new_interval = 0.60

    log_info("Engine started. F5 Console. Space pause. Editor mode: Paint. Type 'help'.")

    while not glfw.window_should_close(window):
        glfw.poll_events()
        impl.process_inputs()

        now = time.time()
        real_dt = now - last_time
        last_time = now

        # F5 toggle console
        f5_down = glfw.get_key(window, glfw.KEY_F5) == glfw.PRESS
        if f5_down and not f5_was_down:
            console_open = not console_open
            log_info("Console " + ("opened." if console_open else "closed."))
        f5_was_down = f5_down

        # Space toggles pause (edge)
        space_down = glfw.get_key(window, glfw.KEY_SPACE) == glfw.PRESS
        if space_down and not space_was_down:
            paused = not paused
            log_info("Paused." if paused else "Playing.")
        space_was_down = space_down

        # Path tool hotkeys
        enter_down = glfw.get_key(window, glfw.KEY_ENTER) == glfw.PRESS
        backspace_down = glfw.get_key(window, glfw.KEY_BACKSPACE) == glfw.PRESS
        esc_down = glfw.get_key(window, glfw.KEY_ESCAPE) == glfw.PRESS

        # compute gameplay dt (time controls)
        dt = compute_game_dt(real_dt)

        # invalid flash timer uses real time (UI feedback doesn't freeze)
        if invalid_flash_t > 0.0:
            invalid_flash_t = max(0.0, invalid_flash_t - real_dt)

        # -------- mouse input (paint/select/path tool) --------
        io = imgui.get_io()
        mouse_over_imgui = io.want_capture_mouse
        keyboard_over_imgui = io.want_capture_keyboard

        hover_cell = None
        mx, my = glfw.get_cursor_pos(window)
        hover_cell = screen_to_grid(mx, my)

        if editor_mode == EDIT_PATH_TOOL and not keyboard_over_imgui:
            # Enter = apply, Backspace = pop, Esc = clear
            if enter_down and not enter_was_down:
                path_tool_apply()
            if backspace_down and not backspace_was_down:
                if path_waypoints:
                    path_waypoints.pop()
                    path_tool_rebuild_preview()
                    log_info("Path waypoint popped.")
            if esc_down and not esc_was_down:
                path_tool_clear()

        enter_was_down = enter_down
        backspace_was_down = backspace_down
        esc_was_down = esc_down

        if not mouse_over_imgui and hover_cell is not None:
            # Right select tower always
            if glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_RIGHT) == glfw.PRESS:
                if hover_cell in towers:
                    selected_tower = hover_cell

            # Left click behavior depends on mode
            if glfw.get_mouse_button(window, glfw.MOUSE_BUTTON_LEFT) == glfw.PRESS:
                if editor_mode == EDIT_PAINT:
                    place_tile(hover_cell, current_brush)
                else:
                    # Path Tool: add waypoint on click (edge-like behavior is hard with polling,
                    # but it's okay: avoid repeats by requiring mouse release is complex.
                    # We'll approximate: only add if last waypoint differs.)
                    if not path_waypoints or path_waypoints[-1] != hover_cell:
                        # disallow placing on WALL/TOWER (it can still be used but tool may fail)
                        path_waypoints.append(hover_cell)
                        path_tool_rebuild_preview()
                        log_info(f"Path waypoint added: {hover_cell}")

        # -------- wave runtime --------
        if dt > 0.0:
            update_wave(dt)

        # -------- update gameplay --------
        if dt > 0.0:
            for e in enemies:
                e.update(dt, computed_path)

            for t in towers.values():
                t.update(dt, enemies, projectiles)

            for p in projectiles:
                p.update(dt)

            enemies[:] = [e for e in enemies if e.active]
            projectiles[:] = [p for p in projectiles if p.active]

        # -------- render --------
        glClear(GL_COLOR_BUFFER_BIT)
        glLoadIdentity()

        # tiles (textured or flat)
        if use_textures and texman.tileset is not None:
            glEnable(GL_TEXTURE_2D)
            glColor4f(1, 1, 1, 1)
            texman.bind(texman.tileset)
            for y in range(GRID_HEIGHT):
                for x in range(GRID_WIDTH):
                    tile = grid[y][x]
                    u0, v0, u1, v1 = texman.tile_uv(tile)
                    draw_textured_quad(x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE, u0, v0, u1, v1)
            texman.unbind()
            glDisable(GL_TEXTURE_2D)
        else:
            for y in range(GRID_HEIGHT):
                for x in range(GRID_WIDTH):
                    draw_rect(
                        x * TILE_SIZE,
                        y * TILE_SIZE,
                        TILE_SIZE - 1,
                        TILE_SIZE - 1,
                        COLORS[grid[y][x]],
                    )

        # grid lines
        draw_grid_lines()

        # computed path overlay
        if computed_path:
            for (px, py) in computed_path[1:-1]:
                draw_rect_alpha(
                    px * TILE_SIZE + 10,
                    py * TILE_SIZE + 10,
                    TILE_SIZE - 21,
                    TILE_SIZE - 21,
                    PATH_HIGHLIGHT,
                    alpha=0.35
                )

        # path tool preview overlay + waypoints
        if editor_mode == EDIT_PATH_TOOL:
            # preview cells
            for (px, py) in path_preview_cells:
                draw_rect_alpha(px * TILE_SIZE + 12, py * TILE_SIZE + 12, TILE_SIZE - 25, TILE_SIZE - 25, (0.3, 1.0, 0.7), alpha=0.22)
            # waypoint markers
            for i, (wx, wy) in enumerate(path_waypoints):
                c = (1.0, 1.0, 1.0) if i not in (0, len(path_waypoints) - 1) else (1.0, 0.9, 0.2)
                draw_rect_alpha(wx * TILE_SIZE + 16, wy * TILE_SIZE + 16, TILE_SIZE - 33, TILE_SIZE - 33, c, alpha=0.7)

        # invalid placement overlay
        if invalid_flash_t > 0.0 and last_invalid_cell:
            ix, iy = last_invalid_cell
            draw_rect_alpha(
                ix * TILE_SIZE + 6,
                iy * TILE_SIZE + 6,
                TILE_SIZE - 13,
                TILE_SIZE - 13,
                INVALID_OVERLAY,
                alpha=0.35
            )

        # ghost placement preview (Tower brush)
        if editor_mode == EDIT_PAINT and current_brush == TOWER_TILE and hover_cell is not None and not mouse_over_imgui:
            ok, reason = tower_place_valid(hover_cell)
            col = PREVIEW_OK if ok else PREVIEW_BAD

            gx, gy = hover_cell
            draw_rect_alpha(gx * TILE_SIZE + 2, gy * TILE_SIZE + 2, TILE_SIZE - 5, TILE_SIZE - 5, col, alpha=0.22)

            # ghost tower draw (sprite or quad)
            if use_textures and texman.tower is not None:
                glEnable(GL_TEXTURE_2D)
                glColor4f(1, 1, 1, 0.55)
                texman.bind(texman.tower)
                draw_textured_quad(gx * TILE_SIZE + 8, gy * TILE_SIZE + 8, TILE_SIZE - 16, TILE_SIZE - 16, 0, 0, 1, 1)
                texman.unbind()
                glDisable(GL_TEXTURE_2D)
            else:
                draw_rect_alpha(gx * TILE_SIZE + 10, gy * TILE_SIZE + 10, TILE_SIZE - 21, TILE_SIZE - 21, COLORS[TOWER_TILE], alpha=0.35)

            # range preview uses level 1 stats
            cx, cy = tile_center_px(hover_cell)
            r = tower_stats(1)["range"] * TILE_SIZE
            draw_filled_circle(cx, cy, r, RANGE_COLOR, alpha=0.10, segments=72)
            draw_circle(cx, cy, r, RANGE_COLOR, segments=72)

            # small reason label area
            if not ok:
                draw_rect_alpha(gx * TILE_SIZE + 2, gy * TILE_SIZE + TILE_SIZE - 18, TILE_SIZE - 5, 14, (0.0, 0.0, 0.0), alpha=0.55)

        # selected tower range circle
        if show_range and selected_tower in towers:
            t = towers[selected_tower]
            s = t.stats()
            center_x, center_y = tile_center_px((t.cx, t.cy))
            r = float(s["range"]) * TILE_SIZE
            draw_filled_circle(center_x, center_y, r, RANGE_COLOR, alpha=0.16)
            draw_circle(center_x, center_y, r, RANGE_COLOR, segments=72)

        # enemies (sprite or quad) + hp bars
        for e in enemies:
            ex = e.x * TILE_SIZE + TILE_SIZE * 0.25
            ey = e.y * TILE_SIZE + TILE_SIZE * 0.25

            if use_textures and texman.enemy is not None:
                glEnable(GL_TEXTURE_2D)
                glColor4f(1, 1, 1, 1)
                texman.bind(texman.enemy)
                draw_textured_quad(ex, ey, TILE_SIZE * 0.5, TILE_SIZE * 0.5, 0, 0, 1, 1)
                texman.unbind()
                glDisable(GL_TEXTURE_2D)
            else:
                draw_rect(ex, ey, TILE_SIZE * 0.5, TILE_SIZE * 0.5, ENEMY_COLOR)

            hp_frac = max(0.0, min(1.0, e.hp / max(1, e.max_hp)))
            bx = e.x * TILE_SIZE + 6
            by = e.y * TILE_SIZE + 6
            bw = TILE_SIZE - 12
            bh = 6
            draw_rect(bx, by, bw, bh, (0.2, 0.2, 0.2))
            draw_rect(bx, by, bw * hp_frac, bh, (0.2, 1.0, 0.2))

        # towers sprite overlay (nice touch)
        if use_textures and texman.tower is not None:
            glEnable(GL_TEXTURE_2D)
            glColor4f(1, 1, 1, 1)
            texman.bind(texman.tower)
            for (tx, ty), tw in towers.items():
                draw_textured_quad(tx * TILE_SIZE + 8, ty * TILE_SIZE + 8, TILE_SIZE - 16, TILE_SIZE - 16, 0, 0, 1, 1)
            texman.unbind()
            glDisable(GL_TEXTURE_2D)

        # projectiles
        for p in projectiles:
            px = p.x * TILE_SIZE
            py = p.y * TILE_SIZE
            draw_rect(px - 3, py - 3, 6, 6, PROJECTILE_COLOR)

        # -------- ImGui --------
        imgui.new_frame()

        # ---------------- Brush window ----------------
        if show_brush_window:
            imgui.begin("Brush", True)

            # editor mode selector
            imgui.text("Editor Mode")
            if imgui.radio_button("Paint", editor_mode == EDIT_PAINT):
                editor_mode = EDIT_PAINT
            imgui.same_line()
            if imgui.radio_button("Path Tool", editor_mode == EDIT_PATH_TOOL):
                editor_mode = EDIT_PATH_TOOL
                path_tool_rebuild_preview()

            imgui.separator()

            if editor_mode == EDIT_PAINT:
                imgui.text("Paint Brush")
                if imgui.radio_button("Wall (blocks)", current_brush == WALL):
                    current_brush = WALL
                if imgui.radio_button("Path", current_brush == PATH):
                    current_brush = PATH
                if imgui.radio_button(f"Tower (cost {tower_cost(1)}$)", current_brush == TOWER_TILE):
                    current_brush = TOWER_TILE
                if imgui.radio_button("Erase", current_brush == EMPTY):
                    current_brush = EMPTY
                imgui.separator()
                if imgui.radio_button("Start", current_brush == START):
                    current_brush = START
                if imgui.radio_button("End", current_brush == END):
                    current_brush = END
            else:
                imgui.text("Path Tool")
                imgui.text("LMB: add waypoint | Backspace: pop | Enter: apply | Esc: clear")
                imgui.separator()
                _, path_tool_clear_existing_path = imgui.checkbox("Clear existing PATH before apply", path_tool_clear_existing_path)
                imgui.text(f"Waypoints: {len(path_waypoints)}  Preview cells: {len(path_preview_cells)}")
                if imgui.button("Apply Path"):
                    path_tool_apply()
                imgui.same_line()
                if imgui.button("Clear Tool"):
                    path_tool_clear()
                imgui.same_line()
                if imgui.button("Pop"):
                    if path_waypoints:
                        path_waypoints.pop()
                        path_tool_rebuild_preview()
                        log_info("Path waypoint popped.")

            imgui.separator()

            if imgui.button("Rebuild path"):
                rebuild_path()

            imgui.same_line()
            if imgui.button("Wave start"):
                wave_start()

            imgui.same_line()
            if imgui.button("Wave stop"):
                wave_stop()

            imgui.same_line()
            if imgui.button("Spawn normal"):
                spawn_enemy("normal")

            imgui.text(f"Start: {start_cell}")
            imgui.text(f"End:   {end_cell}")
            imgui.text(f"Path length: {len(computed_path)}")
            imgui.text(f"Towers: {len(towers)}  Enemies: {len(enemies)}")
            imgui.text(f"Money: {money}$  Lives: {lives}")

            if start_cell and end_cell and not computed_path:
                imgui.text_colored("NO PATH FOUND (need PATH tiles between Start and End)", 1.0, 0.3, 0.3)

            if invalid_flash_t > 0.0:
                imgui.text_colored("Invalid action (see console)", 1.0, 0.3, 0.3)

            imgui.separator()
            imgui.text("Save/Load")
            _, save_filename = imgui.input_text("Save file", save_filename, 256)
            if imgui.button("Save"):
                save_to_file(save_filename)
            _, load_filename = imgui.input_text("Load file", load_filename, 256)
            if imgui.button("Load"):
                load_from_file(load_filename)

            imgui.end()

        # ---------------- Gameplay / Tower Inspector ----------------
        if show_gameplay_window:
            imgui.begin("Gameplay", True)

            # Time controls
            imgui.text("Time (Space toggles pause)")
            if imgui.button("Play" if paused else "Pause"):
                paused = not paused
                log_info("Paused." if paused else "Playing.")
            imgui.same_line()
            if imgui.button("Step"):
                paused = True
                _step_once = True
                log_info("Step.")
            imgui.same_line()
            if imgui.button("x1"):
                time_scale = 1.0
            imgui.same_line()
            if imgui.button("x2"):
                time_scale = 2.0
            imgui.same_line()
            if imgui.button("x4"):
                time_scale = 4.0
            _, time_scale = imgui.slider_float("Scale", float(time_scale), 0.0, 8.0)

            imgui.separator()
            imgui.text("Render")
            _, use_textures = imgui.checkbox("Use textures (assets/*)", use_textures)
            _, show_grid_lines = imgui.checkbox("Show grid lines", show_grid_lines)

            imgui.separator()
            _, show_range = imgui.checkbox("Show selected tower range", show_range)
            _, wave_enabled = imgui.checkbox("Wave enabled", wave_enabled)

            imgui.separator()
            imgui.text("Selected tower (RMB)")
            if selected_tower is None or selected_tower not in towers:
                imgui.text("None")
            else:
                t = towers[selected_tower]
                s = t.stats()
                imgui.text(f"Cell: {selected_tower}")
                imgui.text(f"Level: {t.level}")
                imgui.text(f"Range: {s['range']:.2f} tiles")
                imgui.text(f"Damage: {int(s['damage'])}")
                imgui.text(f"Rate: {s['rate']:.2f} shots/s")

                curr_mode_idx = TARGET_MODES.index(t.target_mode) if t.target_mode in TARGET_MODES else 0
                changed, new_idx = imgui.combo("Target", curr_mode_idx, TARGET_MODES)
                if changed:
                    t.target_mode = TARGET_MODES[new_idx]
                    log_info(f"Tower target mode: {t.target_mode}")

                up_cost = tower_upgrade_cost(t.level)
                imgui.text(f"Upgrade cost: {up_cost}$")
                if imgui.button("Upgrade"):
                    if money >= up_cost:
                        money -= up_cost
                        t.level += 1
                        log_info(f"Tower upgraded to L{t.level} (-{up_cost}$). Money: {money}")
                    else:
                        log_warn("Not enough money to upgrade.")

                imgui.same_line()
                if imgui.button("Sell"):
                    sell_value = max(10, int((tower_cost(1) + sum(tower_upgrade_cost(i) for i in range(1, t.level))) * 0.5))
                    money += sell_value
                    grid[t.cy][t.cx] = EMPTY
                    del towers[(t.cx, t.cy)]
                    selected_tower = None
                    rebuild_path()
                    log_info(f"Tower sold (+{sell_value}$). Money: {money}")

            imgui.separator()
            imgui.text("Economy")
            imgui.text(f"Money: {money}$")
            imgui.text(f"Lives: {lives}")

            imgui.end()

        # ---------------- Wave Editor ----------------
        if show_wave_window:
            imgui.begin("Wave Editor", True)

            imgui.text("Wave = list of entries. Each entry spawns count enemies every interval seconds.")
            imgui.text(f"Running: {wave_running}  Index: {wave_entry_index}  Remaining: {wave_spawn_remaining}")

            imgui.separator()
            if imgui.button("Start wave"):
                wave_start()
            imgui.same_line()
            if imgui.button("Stop wave"):
                wave_stop()

            imgui.separator()
            imgui.text("Entries")
            remove_idx = None

            for i, entry in enumerate(wave_plan):
                imgui.push_id(str(i))
                imgui.separator()

                current = entry.enemy_type if entry.enemy_type in ENEMY_DEFS else "normal"
                curr_idx = enemy_def_names.index(current)
                changed, new_idx = imgui.combo("Type", curr_idx, enemy_def_names)
                if changed:
                    entry.enemy_type = enemy_def_names[new_idx]

                _, entry.count = imgui.slider_int("Count", int(entry.count), 0, 300)
                _, entry.interval = imgui.slider_float("Interval (s)", float(entry.interval), 0.02, 2.5)

                if imgui.button("Up") and i > 0:
                    wave_plan[i - 1], wave_plan[i] = wave_plan[i], wave_plan[i - 1]
                imgui.same_line()
                if imgui.button("Down") and i < len(wave_plan) - 1:
                    wave_plan[i + 1], wave_plan[i] = wave_plan[i], wave_plan[i + 1]
                imgui.same_line()
                if imgui.button("Remove"):
                    remove_idx = i

                imgui.pop_id()

            if remove_idx is not None and 0 <= remove_idx < len(wave_plan):
                del wave_plan[remove_idx]
                if not wave_plan:
                    wave_plan.append(WaveEntry("normal", 10, 0.6))
                log_info("Wave entry removed.")

            imgui.separator()
            imgui.text("Add new entry")
            _, new_enemy_idx = imgui.combo("New type", new_enemy_idx, enemy_def_names)
            _, new_count = imgui.slider_int("New count", int(new_count), 0, 300)
            _, new_interval = imgui.slider_float("New interval", float(new_interval), 0.02, 2.5)
            if imgui.button("Add entry"):
                wave_plan.append(WaveEntry(enemy_def_names[new_enemy_idx], int(new_count), float(new_interval)))
                log_info("Wave entry added.")

            imgui.end()

        # ---------------- Debug ----------------
        if show_debug_window:
            imgui.begin("Debug", True)
            fps = (1.0 / real_dt) if real_dt > 0 else 0.0
            imgui.text(f"FPS: {fps:.1f}")
            imgui.text(f"Game dt: {dt:.4f}  Paused: {paused}  Scale: {time_scale:.2f}")
            imgui.text(f"Editor mode: {editor_mode}")
            imgui.text(f"Console: {'OPEN (F5)' if console_open else 'closed (F5)'}")
            imgui.end()

        # ---------------- Console (F5) ----------------
        if console_open:
            imgui.begin("Console", True)
            imgui.text("Enter command and press Enter. ↑/↓ history. Type: help")
            imgui.separator()

            # Filters
            global log_filter_debug, log_filter_info, log_filter_warn, log_filter_error
            _, log_filter_debug = imgui.checkbox("DEBUG", log_filter_debug)
            imgui.same_line()
            _, log_filter_info = imgui.checkbox("INFO", log_filter_info)
            imgui.same_line()
            _, log_filter_warn = imgui.checkbox("WARN", log_filter_warn)
            imgui.same_line()
            _, log_filter_error = imgui.checkbox("ERROR", log_filter_error)

            imgui.separator()

            shown = 0
            for entry in reversed(console_log):
                if not _filter_allows(entry.level):
                    continue
                line = entry.text
                if entry.level == LOG_ERROR:
                    imgui.text_colored(line, 1.0, 0.3, 0.3)
                elif entry.level == LOG_WARN:
                    imgui.text_colored(line, 1.0, 0.8, 0.3)
                elif entry.level == LOG_DEBUG:
                    imgui.text_colored(line, 0.6, 0.6, 0.6)
                else:
                    imgui.text_unformatted(line)
                shown += 1
                if shown >= 22:
                    break

            imgui.separator()

            flags = imgui.INPUT_TEXT_ENTER_RETURNS_TRUE
            submitted, console_input = imgui.input_text("##cmd", console_input, 256, flags)
            active = imgui.is_item_active()

            if active:
                if imgui.is_key_pressed(imgui.KEY_UP_ARROW):
                    if history:
                        if history_index == -1:
                            history_index = len(history) - 1
                        else:
                            history_index = max(0, history_index - 1)
                        console_input = history[history_index]
                if imgui.is_key_pressed(imgui.KEY_DOWN_ARROW):
                    if history:
                        if history_index == -1:
                            pass
                        else:
                            history_index += 1
                            if history_index >= len(history):
                                history_index = -1
                                console_input = ""
                            else:
                                console_input = history[history_index]

            imgui.same_line()
            run_clicked = imgui.button("Run")

            imgui.same_line()
            if imgui.button("Clear log"):
                console_log.clear()
                log_info("Console cleared.")

            if (submitted or run_clicked) and console_input.strip():
                cmd = console_input.strip()
                console_input = ""
                history.append(cmd)
                history = history[-60:]
                history_index = -1
                apply_console_command(cmd)

            imgui.end()

        imgui.render()
        impl.render(imgui.get_draw_data())
        glfw.swap_buffers(window)

    impl.shutdown()
    glfw.terminate()


if __name__ == "__main__":
    main()