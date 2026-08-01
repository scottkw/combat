#!/usr/bin/env python3
"""Combat - an Atari 2600 "Combat" (tank) clone in pygame.

Two tanks, a maze, one bullet in flight each, 2:16 on the clock.
Run:  ./venv/bin/python combat.py            play
      ./venv/bin/python combat.py --selftest headless asserts, exits non-zero on failure
      ./venv/bin/python combat.py --shot out.png   render one frame headless and save it
"""

import collections
import math
import os
import random
import struct
import sys

# --- headless modes must be decided before pygame opens any device ---
ARGS = sys.argv[1:]
HEADLESS = "--selftest" in ARGS or "--shot" in ARGS
if HEADLESS:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

# ---------------------------------------------------------------- config ---
CELL = 16
GRID_W, GRID_H = 20, 13
BAR_H = 32
PLAY_W, PLAY_H = GRID_W * CELL, GRID_H * CELL     # 320 x 208
SCREEN_W, SCREEN_H = PLAY_W, PLAY_H + BAR_H       # 320 x 240 internal
SCALE = 3                                          # -> 960 x 720 window
FPS = 60
MATCH_SECONDS = 136                                # 2:16, as on the 2600

TURN_RATE = 165.0        # deg/sec
FWD_SPEED = 60.0         # px/sec
REV_SPEED = 34.0
BULLET_SPEED = 155.0
BULLET_LIFE = 1.8        # sec
BULLET_BOUNCES = 3       # in bounce mode
RESPAWN_TIME = 1.5
TANK_R = 5               # half-width of the square hitbox
HIT_R = 6

BG = (20, 74, 96)
WALL = (214, 170, 84)
WALL_HI = (240, 208, 132)
WALL_LO = (152, 112, 44)
BAR_BG = (8, 24, 34)
INK = (226, 232, 236)
DIM = (120, 148, 160)
P1_COL = (222, 82, 60)
P2_COL = (96, 168, 236)
SHOT = (252, 236, 176)

# Only the left half is written out; the right half is mirrored from it (the
# 2600 mazes were symmetric, and mirroring makes an asymmetric typo impossible).
HALF_MAZES = [
    ("OPEN FIELD", [
        "##########",
        "#.........",
        "#.........",
        "#....###..",
        "#....###..",
        "#.........",
        "#.1.......",
        "#.........",
        "#....###..",
        "#....###..",
        "#.........",
        "#.........",
        "##########",
    ]),
    ("BUNKERS", [
        "##########",
        "#........#",
        "#..####..#",
        "#..#......",
        "#..#..####",
        "#.....#...",
        "#.1...#...",
        "#.....#...",
        "#..#..####",
        "#..#......",
        "#..####..#",
        "#........#",
        "##########",
    ]),
    ("THE MAZE", [
        "##########",
        "#........#",
        "#.######.#",
        "#.#....#.#",
        "#.#.##.#.#",
        "#...##...#",
        "#.1.##....",
        "#...##...#",
        "#.#.##.#.#",
        "#.#....#.#",
        "#.######.#",
        "#........#",
        "##########",
    ]),
]


def _mirror(rows):
    return [r + r[::-1].replace("1", "2") for r in rows]


MAZES = [(name, _mirror(rows)) for name, rows in HALF_MAZES]

# ----------------------------------------------------------------- sound ---
RATE = 22050


class Sfx:
    """Procedural blips - keeps the bundle asset-free."""

    def __init__(self):
        self.ok = False
        self.muted = False
        try:
            pygame.mixer.init(frequency=RATE, size=-16, channels=1, buffer=512)
            self.ok = True
        except (pygame.error, NotImplementedError):
            return          # no audio device / no mixer build -> play silent
        self.fire = self._snd(self._sweep(760, 220, 0.12, 0.35))
        self.boom = self._snd(self._noise(0.45, 0.5))
        self.blip = self._snd(self._sweep(440, 660, 0.06, 0.3))
        self.horn = self._snd(self._sweep(300, 120, 0.9, 0.35))
        self.engine = [self._snd(self._square(58, 0.4, 0.18)),
                       self._snd(self._square(46, 0.4, 0.18))]
        self.chan = [None, None]
        for i, e in enumerate(self.engine):
            self.chan[i] = e.play(-1)
            self.chan[i].set_volume(0.0)

    # -- waveform builders: 16-bit signed mono --
    def _pack(self, samples):
        return b"".join(struct.pack("<h", max(-32767, min(32767, int(s)))) for s in samples)

    def _sweep(self, f0, f1, dur, vol):
        n = int(RATE * dur)
        out, ph = [], 0.0
        for i in range(n):
            f = f0 + (f1 - f0) * (i / n)
            ph += f / RATE
            env = (1.0 - i / n) ** 1.5
            out.append(32767 * vol * env * (1 if ph % 1.0 < 0.5 else -1))
        return out

    def _square(self, f, dur, vol):
        n = (RATE // int(f)) * max(1, int(dur * f))   # whole periods -> clean loop
        return [32767 * vol * (1 if (i * f / RATE) % 1.0 < 0.5 else -1) for i in range(n)]

    def _noise(self, dur, vol):
        rng = random.Random(7)
        n = int(RATE * dur)
        out, v = [], 0.0
        for i in range(n):
            env = (1.0 - i / n) ** 2
            v = v * 0.6 + rng.uniform(-1, 1) * 0.4     # low-passed -> a thud, not a hiss
            out.append(32767 * vol * env * v)
        return out

    def _snd(self, samples):
        return pygame.mixer.Sound(buffer=self._pack(samples))

    def play(self, s):
        if self.ok and not self.muted:
            s.play()

    def engine_vol(self, i, v):
        if self.ok and self.chan[i]:
            self.chan[i].set_volume(0.0 if self.muted else v)

    def toggle_mute(self):
        self.muted = not self.muted
        for i in range(2):
            self.engine_vol(i, 0.0)


# ------------------------------------------------------------------ maze ---
class Maze:
    def __init__(self, index):
        self.name, self.rows = MAZES[index]
        self.spawns = []
        for y, row in enumerate(self.rows):
            for x, c in enumerate(row):
                if c in "12":
                    self.spawns.append((x * CELL + CELL / 2, y * CELL + CELL / 2))

    def solid(self, cx, cy):
        if cx < 0 or cy < 0 or cx >= GRID_W or cy >= GRID_H:
            return True
        return self.rows[cy][cx] == "#"

    def solid_at(self, px, py):
        return self.solid(int(px // CELL), int(py // CELL))

    def blocked(self, px, py, r=TANK_R):
        for cx in (px - r, px + r):
            for cy in (py - r, py + r):
                if self.solid_at(cx, cy):
                    return True
        return False

    def clear_line(self, x0, y0, x1, y1):
        dist = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(dist / 4))
        for i in range(1, steps):
            t = i / steps
            if self.solid_at(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t):
                return False
        return True

    def next_step(self, start, goal):
        """First cell to move to along the shortest open path (None if same/unreachable)."""
        if start == goal:
            return None
        dist = {goal: 0}
        q = collections.deque([goal])
        while q:
            cx, cy = q.popleft()
            if (cx, cy) == start:
                break
            for n in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if n not in dist and not self.solid(*n):
                    dist[n] = dist[(cx, cy)] + 1
                    q.append(n)
        best = None
        for n in ((start[0] + 1, start[1]), (start[0] - 1, start[1]),
                  (start[0], start[1] + 1), (start[0], start[1] - 1)):
            if n in dist and (best is None or dist[n] < dist[best]):
                best = n
        return best

    def draw(self, surf):
        surf.fill(BG)
        for y, row in enumerate(self.rows):
            for x, c in enumerate(row):
                if c != "#":
                    continue
                r = pygame.Rect(x * CELL, y * CELL, CELL, CELL)
                surf.fill(WALL, r)
                if not self.solid(x, y - 1):
                    surf.fill(WALL_HI, (r.x, r.y, CELL, 2))
                if not self.solid(x, y + 1):
                    surf.fill(WALL_LO, (r.x, r.bottom - 2, CELL, 2))


# ---------------------------------------------------------------- bullet ---
class Bullet:
    def __init__(self, x, y, ang, owner, bounce):
        self.x, self.y = x, y
        self.vx = math.cos(ang) * BULLET_SPEED
        self.vy = math.sin(ang) * BULLET_SPEED
        self.owner = owner
        self.life = BULLET_LIFE
        self.bounces = BULLET_BOUNCES if bounce else 0
        self.alive = True

    def update(self, dt, maze):
        self.life -= dt
        if self.life <= 0:
            self.alive = False
            return
        nx = self.x + self.vx * dt
        if maze.solid_at(nx, self.y):
            if self.bounces:
                self.bounces -= 1
                self.vx = -self.vx
            else:
                self.alive = False
                return
        else:
            self.x = nx
        ny = self.y + self.vy * dt
        if maze.solid_at(self.x, ny):
            if self.bounces:
                self.bounces -= 1
                self.vy = -self.vy
            else:
                self.alive = False
                return
        else:
            self.y = ny

    def draw(self, surf):
        surf.fill(SHOT, (int(self.x) - 1, int(self.y) - 1, 3, 3))


# ------------------------------------------------------------------ tank ---
def tank_sprite(color):
    """Base sprite points right (angle 0). Drawn once, rotated on use."""
    s = pygame.Surface((15, 13), pygame.SRCALPHA)
    dark = tuple(int(c * 0.62) for c in color)
    s.fill(dark, (1, 1, 11, 11))          # treads
    s.fill(color, (1, 3, 11, 7))          # hull
    s.fill(color, (10, 5, 5, 3))          # barrel
    s.fill(tuple(min(255, int(c * 1.25)) for c in color), (3, 5, 5, 3))  # turret
    return s


class Tank:
    def __init__(self, idx, color, keys, maze, ai=False):
        self.idx, self.color, self.keys, self.ai = idx, color, keys, ai
        self.base = tank_sprite(color)
        self.rot_cache = {}
        self.score = 0
        self.bullet = None
        self.reload = 0.0
        self.dead = 0.0
        self.think = 0.0
        self.swerve = 0.0
        self.swerve_dir = 1
        self.swerve_thr = 1
        self.way = None
        self.repath = 0.0
        self.stuck_t = 0.0
        self.last_pos = (0, 0)
        self.moving = False
        self.spawn(maze)

    def spawn(self, maze):
        self.x, self.y = maze.spawns[self.idx]
        self.ang = 0.0 if self.idx == 0 else math.pi
        self.bullet = None
        self.reload = 0.0
        self.hidden = False

    def rect(self):
        return pygame.Rect(self.x - HIT_R, self.y - HIT_R, HIT_R * 2, HIT_R * 2)

    # -- input -> (turn, throttle, fire) --
    def read_keys(self, pressed):
        k = self.keys
        turn = (1 if pressed[k["right"]] else 0) - (1 if pressed[k["left"]] else 0)
        thr = (1 if pressed[k["fwd"]] else 0) - (1 if pressed[k["back"]] else 0)
        return turn, thr, pressed[k["fire"]]

    def think_ai(self, dt, foe, maze, rng):
        """Shoot on a clear line, otherwise walk the shortest path to the foe."""
        self.think -= dt
        self.repath -= dt
        los = maze.clear_line(self.x, self.y, foe.x, foe.y)

        if los:
            tx, ty = foe.x, foe.y
        else:
            if self.repath <= 0 or self.way is None:
                here = (int(self.x // CELL), int(self.y // CELL))
                there = (int(foe.x // CELL), int(foe.y // CELL))
                self.way = maze.next_step(here, there)
                self.repath = 0.25
            if self.way is None:
                tx, ty = foe.x, foe.y
            else:
                tx = self.way[0] * CELL + CELL / 2
                ty = self.way[1] * CELL + CELL / 2

        diff = (math.atan2(ty - self.y, tx - self.x) - self.ang + math.pi) % (2 * math.pi) - math.pi

        moved = math.hypot(self.x - self.last_pos[0], self.y - self.last_pos[1])
        self.stuck_t = self.stuck_t + dt if moved < 0.4 else 0.0
        self.last_pos = (self.x, self.y)

        if self.swerve > 0:                      # committed to shaking loose
            self.swerve -= dt
            return self.swerve_dir, self.swerve_thr, False
        if self.stuck_t > 0.6:
            self.swerve = rng.uniform(0.3, 0.7)
            self.swerve_dir = rng.choice((-1, 1))
            self.swerve_thr = -1                 # back off, then turn
            self.stuck_t = 0.0
            return self.swerve_dir, -1, False

        turn = 0 if abs(diff) < 0.08 else (1 if diff > 0 else -1)
        thr = 1 if abs(diff) < 1.4 else 0        # do not drive sideways into walls
        fire = los and abs(diff) < 0.13 and self.think <= 0
        if fire:
            self.think = rng.uniform(0.25, 0.7)  # reaction delay, keeps it beatable
        return turn, thr, fire

    def update(self, dt, pressed, foe, maze, game):
        if self.dead > 0:
            self.dead -= dt
            self.moving = False
            if self.dead <= 0:
                self.spawn(maze)
            return
        if self.ai:
            turn, thr, fire = self.think_ai(dt, foe, maze, game.rng)
        else:
            turn, thr, fire = self.read_keys(pressed)

        self.ang = (self.ang + turn * math.radians(TURN_RATE) * dt) % (2 * math.pi)
        spd = (FWD_SPEED if thr > 0 else REV_SPEED) * thr
        self.moving = abs(thr) > 0.01
        if spd:
            nx = self.x + math.cos(self.ang) * spd * dt
            ny = self.y + math.sin(self.ang) * spd * dt
            if not maze.blocked(nx, self.y) and not self._hits(foe, nx, self.y):
                self.x = nx
            if not maze.blocked(self.x, ny) and not self._hits(foe, self.x, ny):
                self.y = ny

        self.reload = max(0.0, self.reload - dt)
        if fire and self.bullet is None and self.reload <= 0:
            bx = self.x + math.cos(self.ang) * 9
            by = self.y + math.sin(self.ang) * 9
            if not maze.solid_at(bx, by):
                self.bullet = Bullet(bx, by, self.ang, self, game.bounce)
                self.reload = 0.25
                game.sfx.play(game.sfx.fire)

    def _hits(self, foe, x, y):
        if foe.dead > 0:
            return False
        return abs(x - foe.x) < HIT_R * 2 - 1 and abs(y - foe.y) < HIT_R * 2 - 1

    def kill(self, game):
        self.dead = RESPAWN_TIME
        self.hidden = True
        game.explode(self.x, self.y)

    def reset_to_spawn(self, maze):
        """Survivor of a hit: straight back to its start corner, frozen with the victim."""
        self.spawn(maze)
        self.dead = RESPAWN_TIME

    def draw(self, surf):
        if self.hidden:
            return
        bucket = int(self.ang / (2 * math.pi) * 64) % 64
        img = self.rot_cache.get(bucket)
        if img is None:
            img = pygame.transform.rotate(self.base, -bucket * (360 / 64))
            self.rot_cache[bucket] = img
        surf.blit(img, img.get_rect(center=(int(self.x), int(self.y))))


# ------------------------------------------------------------------ game ---
KEYS_P1 = {"fwd": pygame.K_w, "back": pygame.K_s, "left": pygame.K_a,
           "right": pygame.K_d, "fire": pygame.K_SPACE}
KEYS_P2 = {"fwd": pygame.K_UP, "back": pygame.K_DOWN, "left": pygame.K_LEFT,
           "right": pygame.K_RIGHT, "fire": pygame.K_RETURN}

MENU, PLAY, OVER = 0, 1, 2


class Game:
    def __init__(self, sfx, seed=None):
        self.sfx = sfx
        self.rng = random.Random(seed)
        self.state = MENU
        self.menu_row = 0
        self.players = 1
        self.maze_i = 1
        self.bounce = False
        self.paused = False
        self.parts = []
        self.font_big = pygame.font.Font(None, 30)
        self.font = pygame.font.Font(None, 16)
        self.font_sm = pygame.font.Font(None, 13)
        self.start_match()

    def start_match(self):
        self.maze = Maze(self.maze_i)
        self.t1 = Tank(0, P1_COL, KEYS_P1, self.maze)
        self.t2 = Tank(1, P2_COL, KEYS_P2, self.maze, ai=(self.players == 1))
        self.tanks = (self.t1, self.t2)
        self.clock_left = float(MATCH_SECONDS)
        self.parts = []

    def explode(self, x, y):
        for _ in range(18):
            a = self.rng.uniform(0, math.tau)
            s = self.rng.uniform(18, 62)
            self.parts.append([x, y, math.cos(a) * s, math.sin(a) * s,
                               self.rng.uniform(0.25, 0.6)])
        self.sfx.play(self.sfx.boom)

    def new_round(self, killed):
        """After any hit both tanks return to their starting corners, as on the 2600."""
        for t in self.tanks:
            if t in killed:
                t.kill(self)
            else:
                t.reset_to_spawn(self.maze)
            t.bullet = None

    # -- one simulation step --
    def update(self, dt, pressed):
        if self.state != PLAY or self.paused:
            for i in range(2):
                self.sfx.engine_vol(i, 0.0)   # no idling engines on menu/pause/game over
            return
        self.clock_left -= dt
        for t, foe in ((self.t1, self.t2), (self.t2, self.t1)):
            t.update(dt, pressed, foe, self.maze, self)
        for i, t in enumerate(self.tanks):
            self.sfx.engine_vol(i, 0.16 if (t.moving and t.dead <= 0) else 0.0)

        killed = []
        for t, foe in ((self.t1, self.t2), (self.t2, self.t1)):
            b = t.bullet
            if not b:
                continue
            b.update(dt, self.maze)
            if b.alive and foe.dead <= 0 and foe.rect().collidepoint(b.x, b.y):
                t.score += 1
                b.alive = False
                if foe not in killed:
                    killed.append(foe)
            elif b.alive and t.dead <= 0 and b.bounces < BULLET_BOUNCES \
                    and t.rect().collidepoint(b.x, b.y):
                t.score = max(0, t.score - 1)   # your own ricochet counts against you
                b.alive = False
                if t not in killed:
                    killed.append(t)
            if not b.alive:
                t.bullet = None
        if killed:
            self.new_round(killed)

        for p in self.parts:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[4] -= dt
        self.parts = [p for p in self.parts if p[4] > 0]

        if self.clock_left <= 0:
            self.clock_left = 0
            self.state = OVER
            self.sfx.play(self.sfx.horn)

    # -- events --
    def key(self, k):
        if self.state == MENU:
            if k in (pygame.K_UP, pygame.K_w):
                self.menu_row = (self.menu_row - 1) % 3
            elif k in (pygame.K_DOWN, pygame.K_s):
                self.menu_row = (self.menu_row + 1) % 3
            elif k in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_a, pygame.K_d):
                d = 1 if k in (pygame.K_RIGHT, pygame.K_d) else -1
                if self.menu_row == 0:
                    self.players = 1 if self.players == 2 else 2
                elif self.menu_row == 1:
                    self.maze_i = (self.maze_i + d) % len(MAZES)
                else:
                    self.bounce = not self.bounce
                self.sfx.play(self.sfx.blip)
            elif k in (pygame.K_RETURN, pygame.K_SPACE):
                self.start_match()
                self.state = PLAY
                self.sfx.play(self.sfx.blip)
        elif self.state == PLAY:
            if k == pygame.K_p:
                self.paused = not self.paused
        elif self.state == OVER:
            if k in (pygame.K_RETURN, pygame.K_SPACE):
                self.state = MENU
        if k == pygame.K_m:
            self.sfx.toggle_mute()

    # -- render --
    def draw(self, surf):
        play = surf.subsurface((0, BAR_H, PLAY_W, PLAY_H))
        self.maze.draw(play)
        for t in self.tanks:
            if t.bullet:
                t.bullet.draw(play)
            t.draw(play)
        for p in self.parts:
            c = SHOT if p[4] > 0.3 else (208, 96, 40)
            play.fill(c, (int(p[0]), int(p[1]), 2, 2))
        if self.state == MENU:
            self.draw_menu(surf)          # menu owns the whole screen
            return
        self.draw_bar(surf)
        if self.state == OVER:
            self.draw_over(surf)
        elif self.paused:
            self.banner(surf, "PAUSED", "P TO RESUME")

    def draw_bar(self, surf):
        surf.fill(BAR_BG, (0, 0, SCREEN_W, BAR_H))
        s1 = self.font_big.render(str(self.t1.score), False, P1_COL)
        s2 = self.font_big.render(str(self.t2.score), False, P2_COL)
        surf.blit(s1, s1.get_rect(center=(58, BAR_H // 2)))
        surf.blit(s2, s2.get_rect(center=(SCREEN_W - 58, BAR_H // 2)))
        m, s = divmod(int(math.ceil(self.clock_left)), 60)
        clk = self.font.render("%d:%02d" % (m, s), False, INK)
        surf.blit(clk, clk.get_rect(center=(SCREEN_W // 2, 11)))
        sub = "%s%s" % (self.maze.name, "  BOUNCE" if self.bounce else "")
        lab = self.font_sm.render(sub, False, DIM)
        surf.blit(lab, lab.get_rect(center=(SCREEN_W // 2, 24)))

    def _center(self, surf, font, text, y, col=INK):
        img = font.render(text, False, col)
        surf.blit(img, img.get_rect(center=(SCREEN_W // 2, y)))

    def banner(self, surf, title, sub):
        box = pygame.Surface((SCREEN_W - 60, 56))
        box.set_alpha(225)
        box.fill(BAR_BG)
        surf.blit(box, (30, SCREEN_H // 2 - 28))
        pygame.draw.rect(surf, INK, (30, SCREEN_H // 2 - 28, SCREEN_W - 60, 56), 1)
        self._center(surf, self.font_big, title, SCREEN_H // 2 - 8)
        self._center(surf, self.font_sm, sub, SCREEN_H // 2 + 14, DIM)

    def draw_menu(self, surf):
        shade = pygame.Surface((SCREEN_W, SCREEN_H))
        shade.set_alpha(238)
        shade.fill(BAR_BG)
        surf.blit(shade, (0, 0))
        self._center(surf, self.font_big, "C O M B A T", 42)
        self._center(surf, self.font_sm, "2600-STYLE TANK DUEL", 60, DIM)
        rows = [("MODE", "1 PLAYER  (VS CPU)" if self.players == 1 else "2 PLAYERS"),
                ("FIELD", MAZES[self.maze_i][0]),
                ("BULLETS", "BOUNCING" if self.bounce else "STRAIGHT")]
        for i, (lab, val) in enumerate(rows):
            y = 92 + i * 20
            sel = i == self.menu_row
            col = INK if sel else DIM
            l = self.font.render(("> " if sel else "  ") + lab, False, col)
            surf.blit(l, (72, y - 6))
            v = self.font.render(val, False, col)
            surf.blit(v, (150, y - 6))
        self._center(surf, self.font_sm, "ARROWS CHANGE   ENTER STARTS", 168, DIM)
        self._center(surf, self.font_sm, "P1  W A S D + SPACE", 190, P1_COL)
        self._center(surf, self.font_sm, "P2  ARROWS + RETURN", 204, P2_COL)
        self._center(surf, self.font_sm, "P PAUSE   M MUTE   ESC QUIT", 222, DIM)

    def draw_over(self, surf):
        if self.t1.score > self.t2.score:
            title, col = "PLAYER 1 WINS", P1_COL
        elif self.t2.score > self.t1.score:
            title, col = ("CPU WINS" if self.players == 1 else "PLAYER 2 WINS"), P2_COL
        else:
            title, col = "DRAW", INK
        self.banner(surf, "", "ENTER FOR MENU")
        self._center(surf, self.font_big, title, SCREEN_H // 2 - 8, col)


# ------------------------------------------------------------------ main ---
def make_screen():
    pygame.display.set_caption("Combat")
    return pygame.display.set_mode((SCREEN_W * SCALE, SCREEN_H * SCALE))


def run():
    pygame.init()
    sfx = Sfx()
    screen = make_screen()
    surf = pygame.Surface((SCREEN_W, SCREEN_H))
    clock = pygame.time.Clock()
    game = Game(sfx)
    running = True
    while running:
        dt = min(clock.tick(FPS) / 1000.0, 0.05)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    if game.state == PLAY:
                        game.state = MENU
                    else:
                        running = False
                else:
                    game.key(e.key)
        game.update(dt, pygame.key.get_pressed())
        game.draw(surf)
        pygame.transform.scale(surf, screen.get_size(), screen)
        pygame.display.flip()
    pygame.quit()


# ------------------------------------------------------------- self-check ---
def selftest():
    pygame.init()
    sfx = Sfx()
    surf = pygame.Surface((SCREEN_W, SCREEN_H))

    for name, rows in MAZES:
        assert len(rows) == GRID_H, (name, "row count", len(rows))
        assert all(len(r) == GRID_W for r in rows), (name, [len(r) for r in rows])
        assert "".join(rows).count("1") == 1 and "".join(rows).count("2") == 1, name
        assert all(r[0] == "#" and r[-1] == "#" for r in rows), name
        assert set(rows[0]) == {"#"} and set(rows[-1]) == {"#"}, name
        half = GRID_W // 2
        assert all(r[:half] == r[half:][::-1].replace("2", "1") for r in rows), \
            "%s is not mirror-symmetric" % name
        # both spawns must sit in the same open region, or someone is walled in
        m = Maze(MAZES.index((name, rows)))
        (sx, sy), (gx, gy) = [(int(p[0] // CELL), int(p[1] // CELL)) for p in m.spawns]
        seen, stack = {(sx, sy)}, [(sx, sy)]
        while stack:
            cx, cy = stack.pop()
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if (nx, ny) not in seen and not m.solid(nx, ny):
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        assert (gx, gy) in seen, "%s: the two spawns cannot reach each other" % name

    g = Game(sfx, seed=1)

    # walls stop tanks
    g.maze = Maze(0)
    t, foe = g.t1, g.t2
    t.x, t.y, t.ang = CELL * 1.5, CELL * 1.5, math.pi      # face the left wall
    foe.x, foe.y = 300, 200
    pressed = collections.defaultdict(bool)     # any keycode -> not pressed
    throttle = collections.defaultdict(bool)    # ...and one with P1 holding forward
    throttle[KEYS_P1["fwd"]] = True
    for _ in range(120):
        t.update(1 / 60, throttle, foe, g.maze, g)
    assert t.x > CELL, "tank walked through the west wall (x=%.1f)" % t.x
    assert not g.maze.blocked(t.x, t.y), "tank ended inside a wall"

    # a bullet kills, scores, and respawns the victim
    g2 = Game(sfx, seed=2)
    g2.maze = Maze(0)
    g2.state = PLAY
    a, b = g2.t1, g2.t2
    b.ai = False                       # keep the target still
    a.x, a.y, a.ang = 100.0, 100.0, 0.0
    b.x, b.y, b.dead = 150.0, 100.0, 0.0
    a.bullet = Bullet(a.x + 9, a.y, 0.0, a, False)
    for _ in range(60):
        g2.update(1 / 60, pressed)
        if b.dead > 0:
            break
    assert b.dead > 0, "bullet passed straight through the target"
    assert a.score == 1, "hit did not score (score=%d)" % a.score
    assert a.bullet is None, "bullet survived its own hit"
    # a hit resets the board: the survivor goes home at once, the wreck burns
    # where it died and both are back on their marks when play resumes
    assert (a.x, a.y) == g2.maze.spawns[0], "shooter did not return to its spawn"
    assert a.dead > 0 and not a.hidden, "shooter should be frozen but still visible"
    assert b.hidden, "victim should be hidden while it explodes"
    for _ in range(int(RESPAWN_TIME * FPS) + 2):
        g2.update(1 / 60, pressed)
    assert a.dead <= 0 and b.dead <= 0 and not b.hidden, "tanks never came back"
    assert (a.x, a.y) == g2.maze.spawns[0] and (b.x, b.y) == g2.maze.spawns[1], \
        "play resumed with a tank away from its starting corner"

    # straight bullets die on walls, bouncing ones reflect
    m = Maze(0)
    straight = Bullet(CELL * 1.5, CELL * 1.5, math.pi, None, False)
    for _ in range(60):
        straight.update(1 / 60, m)
    assert not straight.alive, "straight bullet survived a wall"
    bouncy = Bullet(CELL * 1.5, CELL * 1.5, math.pi, None, True)
    for _ in range(30):
        bouncy.update(1 / 60, m)
    assert bouncy.alive and bouncy.vx > 0, "bouncing bullet failed to reflect"

    # full CPU-vs-CPU match on every maze: no crash, and they actually fight
    total = 0
    for mi in range(len(MAZES)):
        g3 = Game(sfx, seed=10 + mi)
        g3.maze_i = mi
        g3.players = 1
        g3.start_match()
        g3.t1.ai = True
        g3.state = PLAY
        for _ in range(FPS * 45):
            g3.update(1 / 60, pressed)
            g3.draw(surf)
        assert not g3.maze.blocked(g3.t1.x, g3.t1.y), "P1 wedged in a wall on %s" % g3.maze.name
        assert not g3.maze.blocked(g3.t2.x, g3.t2.y), "P2 wedged in a wall on %s" % g3.maze.name
        assert g3.t1.score + g3.t2.score > 0, \
            "45s of CPU vs CPU on %s and nobody landed a shot" % g3.maze.name
        total += g3.t1.score + g3.t2.score
        print("  %-12s 45s sim -> %d : %d" % (g3.maze.name, g3.t1.score, g3.t2.score))
    assert total > 0, "three CPU matches and nobody landed a shot"

    # clock runs out and declares the match over
    g4 = Game(sfx, seed=4)
    g4.state = PLAY
    g4.clock_left = 0.5
    for _ in range(FPS):
        g4.update(1 / 60, pressed)
    assert g4.state == OVER and g4.clock_left == 0, "match never ended"

    print("selftest OK")


def shot(path):
    pygame.init()
    sfx = Sfx()
    surf = pygame.Surface((SCREEN_W, SCREEN_H))
    g = Game(sfx, seed=3)
    g.state = PLAY
    g.t1.ai = True
    g.t1.score, g.t2.score = 3, 2
    g.clock_left = 91
    for _ in range(FPS * 8):
        g.update(1 / 60, collections.defaultdict(bool))
    g.explode(g.t2.x + 20, g.t2.y - 10)
    for _ in range(6):
        g.update(1 / 60, collections.defaultdict(bool))
    g.draw(surf)
    big = pygame.transform.scale(surf, (SCREEN_W * SCALE, SCREEN_H * SCALE))
    pygame.image.save(big, path)
    print("wrote", path)


if __name__ == "__main__":
    if "--selftest" in ARGS:
        selftest()
    elif "--shot" in ARGS:
        shot(ARGS[ARGS.index("--shot") + 1])
    else:
        run()
