"""FastAPI application for the in-memory Digital Tambola game."""

import asyncio
import os
import random
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field


app = FastAPI(title="Digital Tambola")
STYLE_FILE = Path(__file__).with_name("style.css")


class ConnectionManager:
    """Track connected clients and broadcast game updates to each socket."""

    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.active_connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self.lock:
            self.active_connections.discard(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self.lock:
            connections = tuple(self.active_connections)

        disconnected: list[WebSocket] = []
        for connection in connections:
            try:
                await connection.send_json(message)
            except (OSError, RuntimeError, WebSocketDisconnect):
                disconnected.append(connection)

        if disconnected:
            async with self.lock:
                self.active_connections.difference_update(disconnected)


manager = ConnectionManager()


class CallNumberRequest(BaseModel):
    number: int | None = Field(default=None, ge=1, le=99)


class PlayerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=30)


class BannerRequest(BaseModel):
    message_text: str = Field(default="", max_length=120)
    message_image_url: str = Field(default="", max_length=2048)


class GameState:
    """Mutable state shared by all requests handled by this app instance."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.called_numbers: list[int] = []
        self.players: list[dict[str, Any]] = []
        self.message_text = ""
        self.message_image_url = ""
        self.version = 0

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "drawn_numbers": list(self.called_numbers),
            "last_number": self.called_numbers[-1] if self.called_numbers else None,
            "message_text": self.message_text,
            "message_image_url": self.message_image_url,
            "players": [
                {"id": player["id"], "name": player["name"]}
                for player in self.players
            ],
            "version": self.version,
        }

    async def _publish_unlocked(self) -> None:
        await manager.broadcast(
            {"type": "game_state", "data": self._snapshot_unlocked()}
        )

    async def snapshot(self) -> dict[str, Any]:
        async with self.lock:
            return self._snapshot_unlocked()

    async def call_number(self, number: int | None) -> dict[str, Any]:
        async with self.lock:
            if number is None:
                remaining = sorted(set(range(1, 100)) - set(self.called_numbers))
                if not remaining:
                    raise HTTPException(status_code=409, detail="All numbers have been called.")
                number = random.choice(remaining)
            elif number in self.called_numbers:
                raise HTTPException(status_code=409, detail="That number has already been called.")

            self.called_numbers.append(number)
            self.version += 1
            await self._publish_unlocked()
            return self._snapshot_unlocked()

    async def reset_round(self) -> dict[str, Any]:
        async with self.lock:
            self.called_numbers.clear()
            self.version += 1
            await self._publish_unlocked()
            return self._snapshot_unlocked()

    async def register_player(self, name: str) -> dict[str, Any]:
        async with self.lock:
            player_id = len(self.players) + 1
            player = {"id": player_id, "name": name, "ticket": _make_ticket()}
            self.players.append(player)
            self.version += 1
            await self._publish_unlocked()
            return {
                "id": player_id,
                "name": name,
                "ticket": player["ticket"],
            }

    async def update_banner(self, text: str, image_url: str) -> dict[str, Any]:
        async with self.lock:
            self.message_text = text.strip()
            self.message_image_url = image_url.strip()
            self.version += 1
            await self._publish_unlocked()
            return self._snapshot_unlocked()


def _make_ticket() -> list[list[int | None]]:
    """Create a 3-by-9 ticket with five numbers in each row."""
    row_columns: list[set[int]] = [set(), set(), set()]
    columns = list(range(9))
    random.shuffle(columns)

    for column in columns:
        available_rows = [row for row in range(3) if len(row_columns[row]) < 5]
        row_columns[random.choice(available_rows)].add(column)

    for row in row_columns:
        available_columns = list(set(range(9)) - row)
        row.update(random.sample(available_columns, 5 - len(row)))

    ticket: list[list[int | None]] = [[None] * 9 for _ in range(3)]
    for column in range(9):
        if column == 0:
            low, high = 1, 9
        elif column == 8:
            low, high = 80, 99
        else:
            low, high = column * 10, column * 10 + 9

        rows = [row for row in range(3) if column in row_columns[row]]
        values = sorted(random.sample(range(low, high + 1), len(rows)))
        for row, value in zip(rows, values):
            ticket[row][column] = value
    return ticket


game = GameState()


APP_SCRIPT = r"""
const page = document.body.dataset.page;
const state = {
  drawnNumbers: [],
  lastNumber: null,
  ticket: [],
  messageText: '',
  messageImageUrl: '',
  players: [],
};

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    let message = data.detail || data.error || 'Something went wrong.';
    if (Array.isArray(message)) {
      message = message.map((item) => item.msg || String(item)).join(', ');
    }
    throw new Error(message);
  }
  return data;
}

function renderCalledNumbersList() {
  const list = document.getElementById('called-numbers-list');
  if (!list) return;
  list.innerHTML = '';
  if (!state.drawnNumbers.length) {
    const empty = document.createElement('span');
    empty.className = 'empty-muted';
    empty.textContent = 'No numbers called yet';
    list.appendChild(empty);
    return;
  }
  state.drawnNumbers.forEach((number) => {
    const chip = document.createElement('span');
    chip.className = 'called-chip';
    chip.textContent = number;
    list.appendChild(chip);
  });
}

function updateCurrentCall() {
  const currentCall = document.getElementById('current-call');
  if (currentCall) currentCall.textContent = `Last call: ${state.lastNumber ?? '-'}`;
  const lastNumberLabel = document.getElementById('last-number');
  if (lastNumberLabel) lastNumberLabel.textContent = state.lastNumber ?? '-';
  const drawnCount = document.getElementById('drawn-count');
  if (drawnCount) drawnCount.textContent = String(state.drawnNumbers.length);
  renderCalledNumbersList();
}

function renderAdminGrid() {
  const grid = document.getElementById('admin-grid');
  if (!grid) return;
  grid.innerHTML = '';
  for (let number = 1; number <= 99; number += 1) {
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'number-cell';
    const value = document.createElement('span');
    value.textContent = number;
    cell.appendChild(value);
    const alreadyCalled = state.drawnNumbers.includes(number);
    if (alreadyCalled) cell.classList.add('called');
    cell.disabled = alreadyCalled;
    cell.setAttribute('aria-label', alreadyCalled ? `${number}, already called` : `Call ${number}`);
    cell.addEventListener('click', () => callNumber(number));
    grid.appendChild(cell);
  }
}

function renderPlayers() {
  const list = document.getElementById('players-list');
  if (!list) return;
  list.innerHTML = '';
  state.players.forEach((player) => {
    const pill = document.createElement('div');
    pill.className = 'player-pill';
    pill.textContent = player.name;
    list.appendChild(pill);
  });
}

function renderPlayerTicket() {
  const board = document.getElementById('ticket-board');
  if (!board || !state.ticket.length) return;
  board.innerHTML = '';
  for (let rowIndex = 0; rowIndex < 3; rowIndex += 1) {
    for (let colIndex = 0; colIndex < 9; colIndex += 1) {
      const value = state.ticket[rowIndex][colIndex];
      const cell = document.createElement('div');
      cell.className = 'ticket-cell';
      if (value === null || value === undefined) {
        cell.classList.add('empty');
      } else {
        cell.textContent = value;
        if (state.drawnNumbers.includes(Number(value))) cell.classList.add('called');
      }
      board.appendChild(cell);
    }
  }
}

function syncBannerFields() {
  const textInput = document.getElementById('banner-text');
  const imageInput = document.getElementById('banner-image-url');
  if (textInput) textInput.value = state.messageText || '';
  if (imageInput) imageInput.value = state.messageImageUrl || '';
}

function renderLowerBanner() {
  const panel = document.getElementById('lower-banner-panel');
  const text = document.getElementById('lower-banner-text');
  const image = document.getElementById('lower-banner-image');
  if (!panel || !text || !image) return;
  const hasText = Boolean(state.messageText && state.messageText.trim());
  const hasImage = Boolean(state.messageImageUrl && state.messageImageUrl.trim());
  if (!hasText && !hasImage) {
    panel.classList.add('hidden');
    text.textContent = '';
    image.src = '';
    image.classList.remove('visible');
    return;
  }
  panel.classList.remove('hidden');
  text.textContent = hasText ? state.messageText.trim() : '';
  if (hasImage) {
    image.src = state.messageImageUrl.trim();
    image.alt = hasText ? state.messageText.trim() : 'Party banner';
    image.classList.add('visible');
  } else {
    image.src = '';
    image.classList.remove('visible');
  }
}

function applyGameState(payload) {
  state.drawnNumbers = payload.drawn_numbers || [];
  state.lastNumber = payload.last_number;
  state.messageText = payload.message_text || '';
  state.messageImageUrl = payload.message_image_url || '';
  state.players = payload.players || [];
  updateCurrentCall();
  renderPlayers();
  if (page === 'admin') {
    renderAdminGrid();
    syncBannerFields();
  } else {
    renderPlayerTicket();
    renderLowerBanner();
  }
}

async function refreshGameState() {
  try {
    applyGameState(await fetchJson('/api/game-state'));
  } catch (error) {
    console.error('Failed to load game state:', error);
  }
}

async function callNumber(number) {
  const button = document.getElementById('draw-number-btn');
  if (button) button.disabled = true;
  try {
    const payload = number === undefined ? {} : { number };
    applyGameState(await fetchJson('/call-number', {
      method: 'POST',
      body: JSON.stringify(payload),
    }));
  } catch (error) {
    console.error('Number call failed:', error);
    alert(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function registerPlayer(event) {
  event.preventDefault();
  const input = document.getElementById('player-name');
  const name = (input.value || '').trim();
  if (!name) return;
  try {
    const data = await fetchJson('/api/player/register', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
    state.ticket = data.player.ticket;
    document.getElementById('player-title').textContent = data.player.name;
    document.getElementById('join-panel').classList.add('hidden');
    document.getElementById('ticket-panel').classList.remove('hidden');
    renderPlayerTicket();
    await refreshGameState();
  } catch (error) {
    alert(error.message);
  }
}

async function resetGame() {
  try {
    applyGameState(await fetchJson('/api/reset-game', { method: 'POST' }));
  } catch (error) {
    console.error('Reset failed:', error);
    alert(error.message);
  }
}

async function updateBanner(clear = false) {
  const textInput = document.getElementById('banner-text');
  const imageInput = document.getElementById('banner-image-url');
  const payload = clear ? { message_text: '', message_image_url: '' } : {
    message_text: textInput ? textInput.value.trim() : '',
    message_image_url: imageInput ? imageInput.value.trim() : '',
  };
  try {
    applyGameState(await fetchJson('/api/game-banner', {
      method: 'POST',
      body: JSON.stringify(payload),
    }));
  } catch (error) {
    console.error('Banner update failed:', error);
    alert(error.message);
  }
}

if (page === 'admin') {
  document.getElementById('draw-number-btn').addEventListener('click', () => callNumber());
  document.getElementById('reset-game-btn').addEventListener('click', resetGame);
  document.getElementById('update-banner-btn').addEventListener('click', () => updateBanner());
  document.getElementById('clear-banner-btn').addEventListener('click', () => updateBanner(true));
} else {
  document.getElementById('player-form').addEventListener('submit', registerPlayer);
}

let gameSocket;
let reconnectDelay = 1000;
let reconnectTimer = null;

function connectGameSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
  gameSocket = socket;

  socket.addEventListener('open', () => {
    reconnectDelay = 1000;
  });

  socket.addEventListener('message', (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.type === 'game_state') {
        applyGameState(message.data);
      } else if (message.type === 'ping') {
        socket.send(JSON.stringify({ type: 'pong' }));
      }
    } catch (error) {
      console.error('Could not process game update:', error);
    }
  });

  socket.addEventListener('close', () => {
    if (reconnectTimer !== null) return;
    const delay = reconnectDelay + Math.random() * 500;
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      connectGameSocket();
    }, delay);
  });

  socket.addEventListener('error', () => socket.close());
}

connectGameSocket();
window.setInterval(() => {
  if (gameSocket && gameSocket.readyState === WebSocket.OPEN) {
    gameSocket.send(JSON.stringify({ type: 'ping' }));
  }
}, 20000);
refreshGameState();
"""


PLAYER_PAGE = r"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Digital Tambola Player</title>
    <link rel="stylesheet" href="/style.css" />
  </head>
  <body data-page="player">
    <div class="page-shell player-shell">
      <header class="topbar">
        <div>
          <p class="eyebrow">Party Game</p>
          <h1>Digital Bingo</h1>
        </div>
      </header>

      <div id="join-panel" class="join-panel">
        <form id="player-form">
          <label for="player-name">Enter your name</label>
          <input id="player-name" name="player-name" type="text" maxlength="30" placeholder="e.g. Ravi" required />
          <button type="submit" class="primary-btn">Generate Ticket</button>
        </form>
      </div>

      <div id="ticket-panel" class="ticket-panel hidden">
        <div class="ticket-header">
          <h2 id="player-title">Ticket</h2>
          <span id="current-call" class="current-call">Last call: -</span>
        </div>
        <div id="ticket-board" class="ticket-board" aria-live="polite"></div>
      </div>

      <div id="lower-banner-panel" class="lower-banner-panel hidden">
        <div class="lower-banner-content">
          <div id="lower-banner-text" class="lower-banner-text" aria-live="polite"></div>
          <img id="lower-banner-image" class="lower-banner-image" alt="Party banner" />
        </div>
      </div>
    </div>
    <script>__APP_SCRIPT__</script>
  </body>
</html>
"""


ADMIN_PAGE = r"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Digital Tambola Admin</title>
    <link rel="stylesheet" href="/style.css" />
  </head>
  <body data-page="admin">
    <div class="page-shell admin-shell">
      <main class="admin-main-column">
        <header class="topbar">
          <div>
            <p class="eyebrow">Admin Console</p>
            <h1>Digital Tambola</h1>
          </div>
        </header>

        <div class="button-row">
          <button id="draw-number-btn" class="primary-btn">Draw Next Number</button>
          <button id="reset-game-btn" class="secondary-btn">Reset Round</button>
        </div>

        <div class="status-row">
          <div class="status-card">
            <span class="label">Last Number</span>
            <strong id="last-number">-</strong>
          </div>
          <div class="status-card">
            <span class="label">Drawn</span>
            <strong id="drawn-count">0</strong>
          </div>
        </div>

        <div class="admin-content-grid">
          <div class="admin-results-column">
            <div class="called-numbers-panel">
              <h2>Numbers Called</h2>
              <div id="called-numbers-list" class="called-number-list" aria-live="polite"></div>
            </div>

            <div class="board-card">
              <div id="admin-grid" class="number-grid" aria-live="polite"></div>
            </div>

            <section class="players-panel">
              <h2>Players</h2>
              <div id="players-list" class="players-list"></div>
            </section>
          </div>

          <aside class="banner-editor">
            <h2>Lower Screen Banner</h2>
            <div class="banner-form">
              <label for="banner-text">Text message</label>
              <input id="banner-text" type="text" maxlength="120" placeholder="Party vibes, birthday shoutout, or a fun note" />

              <label for="banner-image-url">Image URL</label>
              <input id="banner-image-url" type="url" placeholder="https://example.com/party-image.jpg" />

              <div class="banner-actions">
                <button id="update-banner-btn" class="primary-btn" type="button">Update Lower Screen</button>
                <button id="clear-banner-btn" class="secondary-btn" type="button">Clear</button>
              </div>
            </div>
          </aside>
        </div>
      </main>
    </div>
    <script>__APP_SCRIPT__</script>
  </body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
@app.get("/player", response_class=HTMLResponse)
async def player_page() -> HTMLResponse:
    return HTMLResponse(PLAYER_PAGE.replace("__APP_SCRIPT__", APP_SCRIPT))


@app.get("/admin", response_class=HTMLResponse)
async def admin_page() -> HTMLResponse:
    return HTMLResponse(ADMIN_PAGE.replace("__APP_SCRIPT__", APP_SCRIPT))


@app.get("/style.css")
async def stylesheet() -> FileResponse:
    if not STYLE_FILE.is_file():
        raise HTTPException(status_code=404, detail="Stylesheet not found.")
    return FileResponse(STYLE_FILE, media_type="text/css")


@app.get("/api/game-state")
async def get_game_state() -> dict[str, Any]:
    return await game.snapshot()


@app.get("/api/players")
async def get_players() -> dict[str, Any]:
    return {"players": (await game.snapshot())["players"]}


@app.post("/call-number")
async def call_number(request: CallNumberRequest) -> dict[str, Any]:
    return await game.call_number(request.number)


@app.post("/api/reset-game")
async def reset_game() -> dict[str, Any]:
    return await game.reset_round()


@app.post("/api/player/register")
async def register_player(request: PlayerRequest) -> dict[str, Any]:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Player name cannot be blank.")
    player = await game.register_player(name)
    return {"player": player}


@app.post("/api/game-banner")
async def update_game_banner(request: BannerRequest) -> dict[str, Any]:
    image_url = request.message_image_url.strip()
    if image_url:
        parsed_url = urlsplit(image_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise HTTPException(
                status_code=422,
                detail="Banner image URL must use http or https.",
            )
    return await game.update_banner(request.message_text, image_url)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    try:
        async with game.lock:
            await manager.connect(websocket)
            await websocket.send_json(
                {"type": "game_state", "data": game._snapshot_unlocked()}
            )

        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "WebSocket messages must be JSON objects.",
                    }
                )
            elif message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
