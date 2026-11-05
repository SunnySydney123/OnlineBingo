const page = document.body.dataset.page;
const socket = io();

const state = {
  drawnNumbers: [],
  lastNumber: null,
  ticket: [],
  messageText: '',
  messageImageUrl: '',
};

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || 'Something went wrong.');
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
  if (currentCall) {
    currentCall.textContent = `Last call: ${state.lastNumber ?? '-'}`;
  }

  const lastNumberLabel = document.getElementById('last-number');
  if (lastNumberLabel) {
    lastNumberLabel.textContent = state.lastNumber ?? '-';
  }

  const drawnCount = document.getElementById('drawn-count');
  if (drawnCount) {
    drawnCount.textContent = String(state.drawnNumbers.length);
  }

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
    if (alreadyCalled) {
      cell.classList.add('called');
    }
    cell.disabled = alreadyCalled;
    cell.setAttribute('aria-label', alreadyCalled ? `${number}, already called` : `Call ${number}`);
    cell.addEventListener('click', () => {
      socket.emit('admin_draw', { number });
    });

    grid.appendChild(cell);
  }
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
        if (state.drawnNumbers.includes(Number(value))) {
          cell.classList.add('called');
        }
      }

      board.appendChild(cell);
    }
  }
}

function syncBannerFields() {
  const textInput = document.getElementById('banner-text');
  const imageInput = document.getElementById('banner-image-url');

  if (textInput) {
    textInput.value = state.messageText || '';
  }

  if (imageInput) {
    imageInput.value = state.messageImageUrl || '';
  }
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

  if (hasText) {
    text.textContent = state.messageText.trim();
  } else {
    text.textContent = '';
  }

  if (hasImage) {
    image.src = state.messageImageUrl.trim();
    image.alt = hasText ? state.messageText.trim() : 'Party banner';
    image.classList.add('visible');
  } else {
    image.src = '';
    image.classList.remove('visible');
  }
}

async function refreshGameState() {
  try {
    const data = await fetchJson('/api/game-state');
    state.drawnNumbers = data.drawn_numbers || [];
    state.lastNumber = data.last_number;
    state.messageText = data.message_text || '';
    state.messageImageUrl = data.message_image_url || '';
    updateCurrentCall();

    if (page === 'admin') {
      renderAdminGrid();
      loadPlayers();
      syncBannerFields();
    }

    if (page === 'player') {
      renderPlayerTicket();
      renderLowerBanner();
    }
  } catch (error) {
    console.error('Failed to load game state:', error);
  }
}

async function loadPlayers() {
  const list = document.getElementById('players-list');
  if (!list) return;

  try {
    const data = await fetchJson('/api/players');
    list.innerHTML = '';
    data.players.forEach((player) => {
      const pill = document.createElement('div');
      pill.className = 'player-pill';
      pill.textContent = player.name;
      list.appendChild(pill);
    });
  } catch (error) {
    console.error('Could not load players:', error);
  }
}

async function registerPlayer(event) {
  event.preventDefault();

  const input = document.getElementById('player-name');
  const name = (input.value || '').trim();
  if (!name) {
    return;
  }

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
    const data = await fetchJson('/api/reset-game', { method: 'POST' });
    state.drawnNumbers = data.drawn_numbers || [];
    state.lastNumber = data.last_number;
    updateCurrentCall();
    renderAdminGrid();
    loadPlayers();
  } catch (error) {
    console.error('Reset failed:', error);
  }
}

if (page === 'admin') {
  renderAdminGrid();
  loadPlayers();
  refreshGameState();

  document.getElementById('draw-number-btn').addEventListener('click', () => {
    socket.emit('admin_draw');
  });

  document.getElementById('reset-game-btn').addEventListener('click', resetGame);

  document.getElementById('update-banner-btn').addEventListener('click', async () => {
    const textInput = document.getElementById('banner-text');
    const imageInput = document.getElementById('banner-image-url');

    const payload = {
      message_text: textInput ? (textInput.value || '').trim() : '',
      message_image_url: imageInput ? (imageInput.value || '').trim() : '',
    };

    try {
      const data = await fetchJson('/api/game-banner', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      state.messageText = data.message_text || '';
      state.messageImageUrl = data.message_image_url || '';
      syncBannerFields();
      renderLowerBanner();
    } catch (error) {
      console.error('Banner update failed:', error);
    }
  });

  document.getElementById('clear-banner-btn').addEventListener('click', async () => {
    try {
      const data = await fetchJson('/api/game-banner', {
        method: 'POST',
        body: JSON.stringify({ message_text: '', message_image_url: '' }),
      });

      state.messageText = data.message_text || '';
      state.messageImageUrl = data.message_image_url || '';
      syncBannerFields();
      renderLowerBanner();
    } catch (error) {
      console.error('Banner clear failed:', error);
    }
  });
}

if (page === 'player') {
  document.getElementById('player-form').addEventListener('submit', registerPlayer);
  refreshGameState();
}

socket.on('game_state', (payload) => {
  state.drawnNumbers = payload.drawn_numbers || [];
  state.lastNumber = payload.last_number;
  state.messageText = payload.message_text || '';
  state.messageImageUrl = payload.message_image_url || '';
  updateCurrentCall();

  if (page === 'admin') {
    renderAdminGrid();
    loadPlayers();
    syncBannerFields();
  }

  if (page === 'player') {
    renderPlayerTicket();
    renderLowerBanner();
  }
});

socket.on('connect', () => {
  refreshGameState();
});
