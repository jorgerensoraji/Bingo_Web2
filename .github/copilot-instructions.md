# Copilot / AI assistant instructions — Bingo_Web2

Quick reference for code edits, PRs and feature work in this small Flask + static frontend app.

- **Big picture**: single-process Flask app (`app.py`) serves two UIs: admin game controller and player cartillas. Frontend JS drives most UI logic and calls the JSON HTTP API under `/api/*` implemented in `app.py`.

- **Key files**:
  - `app.py`: Flask server, game state, cartilla generation, TTS, PDF/PNG export. See [app.py](app.py#L1-L40) for main server start and configuration.
  - `templates/index.html` and `templates/cartillas.html`: admin and player pages that include `static/js/game.js` and `static/js/cartillas.js` respectively.
  - `static/js/game.js`: admin-side controls (draw, auto, TTS requests). Uses `adminFetch()` which sets header `X-Admin-Key` from `?key=` in URL.
  - `static/js/cartillas.js`: player flows (generate, list, save manual cartilla); uses `localStorage` client id `bingo_client_id` for presence filtering.
  - `cartillas_data/`: JSON cartillas are persisted here as `<ID>.json` by `save_cartilla()`.

- **Auth & admin workflows**:
  - Admin-protected endpoints require `X-Admin-Key` header or `?key=...` query param. Frontend reads `?key` and injects it via `adminFetch()` in `game.js`.
  - To test admin actions locally: run `py -3.12 app.py` (or `python app.py`) and open `http://localhost:5000/admin?key=admin` (default ADMIN_KEY is `admin` unless `ADMIN_KEY` env var is set).

- **Runtime & dependencies**:
  - Dependencies listed in `requirements.txt`. TTS uses `edge-tts`; PDF/PNG generation uses `reportlab` and `Pillow`.
  - For production use the repo includes `gunicorn` in `requirements.txt`; typical run: `gunicorn -w 4 -b 0.0.0.0:5000 app:app`.

- **Data flows & important invariants**:
  - Game state (available/drawn numbers, session code) is in-memory in `GameState` inside `app.py`. Restart resets game state.
  - Cartilla generation is allowed only when `game.drawn` is empty and the client supplies the current `session_code` (see `/api/admin/session` and `/api/cartilla/generate`). The UI enforces this and will redirect to admin if the game started.
  - Cartillas are simple JSON files stored in `cartillas_data/*.json`. `load_all_cartillas()` reads that dir; deleting files is how the app clears sold cartillas.

- **Project-specific conventions** (important for edits):
  - Admin key can be provided either as header `X-Admin-Key` or as `?key=`; update both client and server logic when changing authentication handling.
  - Client presence uses a generated `bingo_client_id` stored in `localStorage` (key `bingo_client_id`). Frontend relies on this exact key.
  - TTS files are cached under a temp directory (`TTS_DIR`), created at runtime. `make_audio()` writes MP3s to that folder to avoid re-requesting.
  - Cartilla layout logic is split between Python generators (`generate_cartilla_grid()` in `app.py`) and client-side layout/validation in `static/js/cartillas.js` (e.g., `validateManualGrid()`).

- **Editing guidance / safe change areas**:
  - To change visual behavior, edit the corresponding `static/js/*.js` and `static/css/*` files. Keep API shapes stable if possible (endpoints under `/api/*`) so frontend code doesn't break.
  - If you alter `/api/cartilla/generate` or the cartilla JSON shape, update `static/js/cartillas.js` functions that expect `cartilla.grid` to be a 3x9 array.
  - If you modify game state fields in `GameState`, update both `api_state()` and frontend consumers in `game.js` / `cartillas.js` that destructure server responses.

- **Debug / run commands**:
  - Local dev quick run: `py -3.12 app.py` (prints local IP and hosts at port 5000).
  - Prod (example): `gunicorn -w 4 -b 0.0.0.0:5000 app:app` after installing `requirements.txt`.
  - Install deps: `python -m pip install -r requirements.txt`.

- **What to watch out for in PRs**:
  - Don't change the `session_code` lifecycle without updating the player flow: cartilla generation requires equality with `game.session_code` and the UI uses this to block generation once draws begin.
  - Persistence is file-based. Race conditions are possible if multiple processes modify `cartillas_data/` concurrently — the app expects single-process usage.

If anything here is unclear or you want additional examples / runnable snippets (e.g., a small test that creates a cartilla and checks `/api/cartilla/<id>/check`), tell me which part to expand. 
