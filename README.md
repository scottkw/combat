# Combat

An Atari 2600 *Combat* clone in Python/pygame, shipped as a standalone macOS
arm64 binary. Two tanks, a symmetric maze, one shot in flight each, 2:16 on the
clock.

**[Download the macOS build](https://github.com/scottkw/combat/releases/latest)**
— a 13 MB Apple-silicon executable, or the same game as a `.app`. No Python or
pygame needed.

```
./dist/Combat          # single file, after building locally
open dist/Combat.app   # same game, double-clickable from Finder
```

The binary is ad-hoc signed, not notarised, so macOS quarantines anything
downloaded from GitHub and refuses to open it. Clear the flag once:

```
xattr -dr com.apple.quarantine ~/Downloads/Combat-macos-arm64
chmod +x ~/Downloads/Combat-macos-arm64
```

---

## 1. What this was

An autonomy test of Claude Opus 5 in Claude Code, run on 2026-08-01. The premise
Ken set:

> Ok, I want to test Opus 5 and how well it can freely operate without my input
> based on a single prompt.

The working directory was empty and not a git repo. Before the task prompt
arrived, the agent stated its own ground rules for the run: no clarifying
questions, pick sensible defaults and state the assumptions, verify its own work
by running it rather than claiming it works, and stop only for destructive or
irreversible actions.

### The prompt

Verbatim, and the only task instruction given:

> Build a playable Combat clone in Python using the pygame library. The final
> product should be a single executable (your choice on how to accomplish that
> and preferably not requiring having Python install to run it) that will run on
> my MacBook Air M3.

Everything below — game design, maze layouts, the CPU opponent, the packaging
approach, the test suite — was decided without further input.

Two follow-ups came later and are the only other instructions in the session:
write this README, and *"after a tank is shot, the position of both tanks should
go back to their original locations"* — the board-reset rule described under
Rules and Rounds.

### Judgment calls made instead of asking

| Decision | Why |
|---|---|
| Added a CPU opponent | The 1977 original is two-player-only. A single person on a laptop needs something to shoot at. |
| Python 3.13, not the 3.14 on the machine | The 3.14 pygame wheel ships without `pygame.mixer` — no sound at all. Found by running it, not by guessing. |
| PyInstaller `--onefile` | Meets "single executable, no Python installed" literally. A `.app` bundle is emitted alongside for Finder. |
| Three mazes, not 27 game variants | Combat had 27 variations across tanks, biplanes and jets. Three tank fields cover the ask; the rest is listed under Not implemented. |
| 960×720 window, no fullscreen | Fits any MacBook screen at an exact 3× integer scale, so pixels stay square and sharp. |

### What broke along the way

Recorded because a clean final state hides the process:

1. **pygame 3.14 has no mixer module.** `import pygame` succeeded, `pygame.mixer.init()`
   raised `NotImplementedError`. Fixed by rebuilding the venv on 3.13 *and* by
   catching the error so missing audio degrades to silence instead of crashing.
2. **A test bug, not a game bug.** The first bullet-kill assertion failed because
   the harness left the match in `MENU`, where `update()` correctly no-ops.
3. **The mazes weren't mirror-symmetric.** Hand-typed rows had a stray wall column
   on one side. Fixed structurally: only the left half is written down now and the
   right half is generated from it, so an asymmetric typo is impossible.
4. **The CPU couldn't play.** After the maze fix, 45 seconds of CPU-vs-CPU scored
   0:0 on two of three fields — "drive at the enemy and swerve at walls" cannot
   solve a maze. Replaced with BFS pathfinding. Scores went 0:0 → 11:11 and 8:8.
5. **Ladder-rung walls.** Every wall cell drew its own highlight and shadow, so
   stacked cells looked striped. Now only the exposed top and bottom faces are
   shaded, and walls read as solid slabs.
6. **The engine loop kept buzzing on the menu.** Escaping out of a match left the
   looping engine sound at its last volume. Fixed in one place, in the early-out
   of `Game.update`.
7. **A stuck throttle in the test harness.** Adding the board-reset rule, the new
   assertion "both tanks are on their marks when play resumes" failed. The cause
   was the shared synthetic key state: an earlier test had pressed P1's forward
   key and never released it, so the respawned tank drove off its corner. Each
   test now owns its key state. The game was right; the harness was lying.

---

## 2. The game

*Combat* (Atari, 1977) was the cartridge that shipped in the box with the 2600.
Two tanks, a walled playfield, and a two-and-a-quarter-minute round. This clone
follows the tank half of that game.

### Controls

| | Forward / Reverse | Turn | Fire |
|---|---|---|---|
| Player 1 | `W` / `S` | `A` / `D` | `Space` |
| Player 2 | `↑` / `↓` | `←` / `→` | `Return` |

`P` pause · `M` mute · `Esc` back to the menu, again to quit.
On the menu, arrows change a setting and `Return` starts the match.

### Rules

- **One bullet in the air per tank**, as on the 2600. Miss, and you are unarmed
  until it expires (1.8s) or hits something.
- **A hit resets the board.** The shooter scores 1, the victim explodes where it
  died, and both tanks return to their starting corners — the survivor goes home
  immediately and sits frozen for the 1.5s the wreck takes to respawn. You cannot
  camp a kill spot.
- **BOUNCING** bullets ricochet up to 3 times. After the first bounce your own
  shot can kill you, and a self-kill costs you a point.
- Tanks block each other and cannot drive through walls or each other.
- Highest score when the clock reaches 0:00 wins; equal scores draw.

### Options

- **MODE** — 1 PLAYER (vs CPU) or 2 PLAYERS sharing the keyboard.
- **FIELD** — OPEN FIELD (four blocks, long sightlines), BUNKERS (nested
  enclosures), THE MAZE (tight concentric corridors).
- **BULLETS** — STRAIGHT or BOUNCING.

---

## 3. How it works

One file, `combat.py`, 820 lines, standard library plus pygame. No asset files
of any kind — every sound and sprite is generated at startup.

### Rendering

Everything draws to a 320×240 internal surface and is scaled 3× with
nearest-neighbour to a 960×720 window. That is what produces genuine chunky
pixels rather than smooth modern shapes, and it means one constant (`SCALE`)
changes the whole window size. The top 32px is the scorebar; the remaining
320×208 is the playfield.

### Mazes

A maze is 20×13 cells of 16px. Only the left 10 columns are written as text in
`HALF_MAZES`; `_mirror()` reverses each row onto itself and rewrites the `1`
spawn marker into a `2`. Symmetry is therefore structural rather than a thing to
proofread. `Maze` exposes four queries the rest of the game is built on:

- `solid(cx, cy)` — cell lookup, out of bounds counts as solid.
- `blocked(px, py, r)` — corner test for a square hitbox at a pixel position.
- `clear_line(x0, y0, x1, y1)` — 4px-step raycast, used for line of sight.
- `next_step(start, goal)` — BFS distance field from the goal, returning the
  first cell to step toward. This is the CPU's navigation.

### Tanks

Position and heading are floats. Collision uses a 10px square, bullet hits a
slightly more forgiving 12px one. Movement resolves X and Y independently and
reverts each axis on collision, so a tank slides along a wall instead of sticking
to it. Turning is 165°/s, forward 60px/s, reverse
34px/s. The sprite is drawn once as a right-facing surface and rotated into a
64-bucket cache, so rotation costs nothing per frame.

### Rounds

Bullet collisions are collected for the frame before any of them are applied, so
a genuine double-kill scores for both tanks instead of whichever happened to be
checked first. `Game.new_round()` then runs: whoever was hit explodes and hides,
everyone else is sent straight back to their starting corner, both in-flight
bullets are dropped, and both tanks are frozen for 1.5s. `hidden` is what
separates "burning wreck" from "waiting on its mark" — during the freeze the
survivor is visible and immobile at its spawn, which telegraphs the restart.
Tanks with a live `dead` timer are immune, so a bullet cannot register on a tank
mid-reset.

### The CPU opponent

Runs the same `(turn, throttle, fire)` interface a human controller produces —
it is not privileged.

- **Line of sight clear** → steer straight at the player and fire when within
  0.13 rad of aim.
- **No line of sight** → re-path every 0.25s with BFS and steer at the next
  cell's centre.
- **Throttle cut** when the target is more than 1.4 rad off-heading, so it turns
  before driving into a wall.
- **Stuck detection** — under 0.4px of movement for 0.6s triggers a committed
  0.3–0.7s reverse-and-turn.
- **Reaction delay** of 0.25–0.7s after each shot. This is the difficulty knob;
  lower it to make the CPU brutal.

### Sound

`Sfx` synthesises 16-bit mono waveforms into `bytes` at startup and hands them to
`pygame.mixer.Sound(buffer=...)`: a falling square-wave sweep for the cannon, a
low-passed noise burst for the explosion, a rising blip for menu moves, a long
descending horn for the end of the match, and per-tank engine loops built from a
whole number of periods so they loop without a click. Engine channels play
continuously with their volume driven by whether that tank is moving. If the
mixer cannot initialise, `Sfx.ok` stays `False` and every call becomes a no-op —
the game runs silent rather than failing.

### Game loop

`Game` is a small state machine — `MENU`, `PLAY`, `OVER` — plus a pause flag.
`update(dt, pressed)` steps the world; `draw(surf)` renders it. `pressed` is
whatever supports `pressed[keycode]`, which is why the headless tests can drive a
full match with a `defaultdict(bool)` and no display or keyboard. Delta time is
clamped to 50ms so a stall can never teleport a bullet through a wall.

---

## 4. Build

```bash
python3.13 -m venv venv
./venv/bin/pip install pygame pyinstaller

./venv/bin/pyinstaller --noconfirm --clean --onefile --windowed \
    --name Combat --osx-bundle-identifier com.local.combat combat.py
```

Output lands in `dist/`: `Combat` (13 MB single-file arm64 executable) and
`Combat.app` (the same binary wrapped for Finder). Built with Python 3.13.14,
pygame 2.6.1, PyInstaller, on macOS 26.6, Apple silicon.

**Use Python 3.13, not 3.14.** The 3.14 pygame wheel has no `pygame.mixer`. The
game handles that gracefully but plays silent.

PyInstaller warns that `--onefile` plus a `.app` bundle is deprecated and will
error in v7. Both artifacts were launched and work today; if that breaks, switch
to `--onedir --windowed` for the `.app` and keep a separate `--onefile` build for
the standalone binary.

### Running from source

```bash
./venv/bin/python combat.py                 # play
./venv/bin/python combat.py --selftest      # headless asserts, non-zero on failure
./venv/bin/python combat.py --shot out.png  # render one frame headless to a PNG
```

`--selftest` and `--shot` set `SDL_VIDEODRIVER=dummy` and `SDL_AUDIODRIVER=dummy`
before pygame is imported, so they run with no display and no audio device — over
SSH, in CI, wherever.

---

## 5. Verification

`--selftest` is the whole suite and takes a few seconds:

- Every maze is 20×13, fully walled, has exactly one spawn per side, is
  mirror-symmetric, and **both spawns reach each other** by flood fill — nobody
  can be walled in.
- A tank driven into a wall for 120 frames stays outside it and does not end up
  inside geometry.
- A bullet fired at a stationary tank kills it, scores exactly 1, and is cleared.
- That hit resets the round: the survivor is on its spawn immediately and still
  drawn, the victim is hidden while it explodes, and once the freeze expires both
  tanks are alive and standing on their own starting corners.
- A straight bullet dies on a wall; a bouncing one reflects and survives.
- **45 seconds of CPU vs CPU on each of the three fields**, asserting neither tank
  wedges into a wall and that shots actually land — this is what caught the
  pathfinding failure.
- The clock running out ends the match.

It passes against the source *and* against the packaged binary under `env -i`
with no venv and no `PYTHONPATH`, which is the real proof the bundle is
self-contained. Beyond the automated run, the built app was launched on the
actual machine and screenshotted to confirm a window opens and renders.

```
  OPEN FIELD   45s sim -> 12 : 8
  BUNKERS      45s sim -> 11 : 11
  THE MAZE     45s sim -> 8 : 0
selftest OK
```

Before the board-reset rule existed these came out perfectly symmetric (17:17,
11:11, 8:8) — identical AI, mirrored maze, mirrored start, so the tanks killed
each other on the same frame all match long. Resetting to the corners after every
hit breaks that lockstep, which is why the scores now diverge.

---

## 6. Not implemented

Deliberate omissions, in rough order of how much anyone would miss them:

- The other 24 Combat variants: biplanes, jets, guided missiles, invisible tanks,
  machine-gun mode, screen-wrap fields.
- Fullscreen and window resizing (fixed 3× scale).
- Gamepad support (keyboard only).
- Score persistence; nothing is written to disk.
- Code signing and notarisation — the binary is ad-hoc signed. Fine on the
  machine that built it; anywhere it is downloaded, macOS quarantines it and
  needs `xattr -dr com.apple.quarantine` before it will run. Proper signing needs
  a paid Apple Developer ID.

---

## 7. Files

```
combat.py       the entire game, 820 lines
README.md       this file
Combat.spec     PyInstaller spec, regenerated by the build command
dist/Combat     standalone arm64 binary
dist/Combat.app Finder-launchable bundle wrapping the same binary
build/          PyInstaller scratch, safe to delete
venv/           Python 3.13 build environment, safe to delete

`dist/`, `build/`, `venv/` and `__pycache__/` are gitignored — build output does
not belong in the history. The executables are published as release assets
instead.
```
