# TEST-ENGINE — TD Engine + Editor (Python + ImGui)

A lightweight **2D Tower Defense engine/editor** written in Python using **ImGui** for tools/UI and **OpenGL** for rendering.  
Goal: fast level editing, pathfinding testing, tower/enemy logic, wave system — then gradually evolve into a more “engine-like” toolchain.

---

## ✨ Features

### 🧱 Editor
- Grid-based level editor (**Paint mode**)
- Brushes: **Wall / Path / Tower / Erase / Start / End**
- **Placement preview + ghost tower** (green/red validity + range preview)
- **Path Tool mode**: create paths from waypoints (A* between waypoints)
  - **LMB**: add waypoint  
  - **Backspace**: remove last waypoint  
  - **Enter**: apply path  
  - **Esc**: clear tool

### 🧠 Pathfinding
- A* pathfinding from Start → End
- Computed path overlay visualization

### 🏰 Towers
- Tower placement with path-block prevention
- Tower range shown as a real **radius circle**
- Upgrade / Sell
- Target modes:
  - **First / Last / Closest / Strongest**

### 🌊 Waves + Enemies
- Wave editor: type, count, interval
- Enemy types: normal / fast / tank
- HP bars, rewards, lives system

### ⏱️ Time Controls
- Pause / Play / Step
- Time scale: x1 / x2 / x4 + slider

### 🖥️ Console + Logging
- **F5**: in-engine console
- DEBUG/INFO/WARN/ERROR filters
- Command history (↑ / ↓)
- `save_log` to file

### 🎨 Textures / Sprites
- Tileset + enemy + tower sprite support (PNG)
- If assets are missing: auto-generated **placeholder textures**

---

## 📁 Project Structure
 - TEST-ENGINE/
 - assets/
 - tiles.png # optional (placeholder used if missing)
 - enemy.png # optional (placeholder used if missing)
 - tower.png # optional (placeholder used if missing)
 - main.py
 - requirements.txt
 - README.md

---

## ✅ Requirements
- Recommended: **Python 3.13**
  - (Python 3.14 pre-release can break some packages.)
- Windows / Linux / macOS

---

## ⚙️ Installation

### 1) Create a virtual environment
**Windows PowerShell**
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

Suggested requirements.txt:

glfw
PyOpenGL
imgui
pillow
numpy

python main.py

🎮 Controls (Quick)

LMB: paint / waypoint (in Path Tool mode)

RMB: select tower

F5: toggle console

Space: pause/play

Path Tool: Backspace (pop), Enter (apply), Esc (clear)

🧩 Assets (Optional)
Tileset (assets/tiles.png)

A single row with 6 tiles in this order:
EMPTY, WALL, PATH, TOWER, START, END

Recommended tile size: 16×16 or 32×32 (any size works; it will be scaled)

Enemy (assets/enemy.png) + Tower (assets/tower.png)

Any square PNG works (e.g. 16×16, 32×32)

If you don’t provide any PNGs, the engine generates placeholder textures automatically.

🧪 Console Commands (Examples)

help

wave start / wave stop

spawn normal|fast|tank

save map.json / load map.json

save_log log.txt

time pause|play|step

time scale 2

render textures on|off

render grid on|off

mode paint|path

path clear_existing on|off

path apply

path clear

🗺️ Roadmap (Ideas)

Undo/Redo (Ctrl+Z / Ctrl+Y)

Sprite atlas animation (enemy 1×4 frames)

Projectile types: splash/slow/poison

Better placement tooltip (“why invalid”)

Expanded tower inspector (DPS, range preview always-on)

Scene system + prefabs

📜 License

Not set yet. Recommended:

MIT License (simple and permissive)

or GPL (keeps derivatives open-source)