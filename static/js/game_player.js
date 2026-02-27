/* ═══════════════════════════════════════════════════
   BINGO PRO — game_player.js  v5.0
   Pantalla de espectador/jugador — SOLO lectura.
   Mejoras v5:
   - Sonido para jugadores via /api/speak
   - Desconexión automática si admin inactivo 5 min
   - Reset automático si el admin cierra sesión
   - Notificación visual cuando el admin se desconecta
═══════════════════════════════════════════════════ */

const GROUP_COLORS = [
  { fg:'#5dade2', bg:'#0a1e2e' },
  { fg:'#f4d03f', bg:'#1e1500' },
  { fg:'#f1948a', bg:'#2a0805' },
  { fg:'#e59866', bg:'#2a1000' },
  { fg:'#58d68d', bg:'#051e0f' },
  { fg:'#a569bd', bg:'#160525' },
  { fg:'#48c9b0', bg:'#032420' },
  { fg:'#7fb3d3', bg:'#061320' },
  { fg:'#95a5a6', bg:'#0e1315' },
];
const GROUP_LABELS = ['1–9','10–19','20–29','30–39','40–49','50–59','60–69','70–79','80–90'];

// ── ESTADO ────────────────────────────────────────
let drawnLocal    = [];
let lastLocal     = null;
let clockJob      = null;
let elapsedSec    = 0;
let gameStarted   = false;
let gameId        = null;
let lastPhraseKey = null;   // tracks which phrase we already played
let soundEnabled  = false;
let currentAudio  = null;
let testAudio     = null;  // separate from currentAudio so syncState doesn't kill it
let adminWasOnline = true;
let resetPending   = false;

// ── MY CARTILLA ───────────────────────────────────
let myCartillaId = null;
let myCartilla   = null;
let myBingoFired = false;

// ── TOAST ─────────────────────────────────────────
let toastJob = null;
function showToast(msg, duration) {
  duration = duration || 2800;
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastJob);
  toastJob = setTimeout(function() { t.classList.remove('show'); }, duration);
}

// ── NAVIGATION ────────────────────────────────────
function goToGame() { location.href = '/'; }

// ── INIT GRID ─────────────────────────────────────
function initGrid() {
  const headers = document.getElementById('group-headers');
  const grid    = document.getElementById('num-grid');
  if (!headers || !grid) return;

  GROUP_LABELS.forEach(function(lbl, g) {
    const h = document.createElement('div');
    h.className   = 'group-header';
    h.textContent = lbl;
    h.style.color      = GROUP_COLORS[g].fg;
    h.style.background = GROUP_COLORS[g].bg;
    headers.appendChild(h);
  });

  for (let col = 0; col < 9; col++) {
    for (let row = 0; row < 10; row++) {
      const num = col * 10 + row + 1;
      if (num > 90) continue;
      const cell = document.createElement('div');
      cell.className    = 'num-cell';
      cell.id           = 'cell-' + num;
      cell.textContent  = num;
      cell.style.gridColumn = col + 1;
      cell.style.gridRow    = row + 1;
      grid.appendChild(cell);
    }
  }
}

function initGridReset() {
  for (let n = 1; n <= 90; n++) {
    const cell = document.getElementById('cell-' + n);
    if (!cell) continue;
    cell.classList.remove('drawn', 'just-drawn');
    cell.style.color       = '';
    cell.style.background  = '';
    cell.style.borderColor = '';
  }
}

// ── AUDIO ─────────────────────────────────────────
function stopAudio() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio.src = '';
    currentAudio     = null;
  }
}

function stopTestAudio() {
  if (testAudio) {
    testAudio.pause();
    testAudio.src = '';
    testAudio     = null;
  }
}

function playPhrase(text, voice) {
  if (!soundEnabled || !text) return;
  stopAudio();
  voice = voice || 'es-PE-CamilaNeural';

  fetch('/api/speak', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ text: text, voice: voice })
  })
  .then(function(r) {
    if (!r.ok) throw new Error('speak HTTP ' + r.status);
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('audio')) throw new Error('speak returned non-audio: ' + ct);
    return r.blob();
  })
  .then(function(blob) {
    if (!blob || blob.size < 100) throw new Error('empty audio blob');
    const url    = URL.createObjectURL(blob);
    currentAudio = new Audio(url);
    currentAudio.volume = 0.9;
    var playPromise = currentAudio.play();
    if (playPromise && playPromise.catch) {
      playPromise.catch(function(e) {
        console.warn('Audio play blocked:', e);
        showToast('🔇 Haz clic en la página para activar sonido');
      });
    }
    currentAudio.onended = function() {
      URL.revokeObjectURL(url);
      currentAudio = null;
    };
  })
  .catch(function(e) {
    console.error('playPhrase error:', e);
    showToast('⚠️ Error de audio: ' + e.message);
  });
}

function enableSound() {
  soundEnabled = true;
  const btn = document.getElementById('btn-sound');
  if (btn) {
    btn.textContent       = '🔊 Sonido ON';
    btn.style.background  = 'var(--accent)';
    btn.style.color       = '#041015';
    btn.style.borderColor = 'var(--accent)';
  }

  // Play a REAL TTS test phrase so the user hears the actual voice
  const voice = (document.getElementById('player-voice-select') || {}).value || 'es-PE-CamilaNeural';
  showToast('🔊 Probando sonido…');

  fetch('/api/speak', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ text: 'Sonido activado. ¡Buena suerte!', voice: voice })
  })
  .then(function(r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.blob();
  })
  .then(function(blob) {
    if (!blob || blob.size < 100) throw new Error('empty');
    stopTestAudio();
    const url  = URL.createObjectURL(blob);
    testAudio  = new Audio(url);
    testAudio.volume = 0.9;
    var p = testAudio.play();
    if (p && p.catch) p.catch(function(e) {
      // Only disable if truly blocked by browser policy (NotAllowedError)
      // NOT if interrupted by stopTestAudio (AbortError)
      if (e.name === 'NotAllowedError') {
        showToast('❌ El navegador bloqueó el audio. Intenta de nuevo.');
        soundEnabled = false;
        if (btn) { btn.textContent = '🔈 Activar sonido'; btn.style.background = ''; btn.style.color = ''; }
      }
    });
    testAudio.onended = function() {
      URL.revokeObjectURL(url);
      testAudio = null;
      showToast('✅ Sonido OK — escucharás cada bolilla');
    };
  })
  .catch(function(e) {
    console.error('Test audio error:', e);
    showToast('❌ Error de audio: ' + e.message);
  });
}

// ── ADMIN OFFLINE HANDLING ────────────────────────
function handleAdminOffline() {
  if (!adminWasOnline || resetPending) return;
  adminWasOnline = false;
  resetPending   = true;

  const statusEl = document.getElementById('sync-status');
  if (statusEl) {
    statusEl.innerHTML = '<span style="color:var(--danger)">⚠️ Admin desconectado — reiniciando en 5s…</span>';
  }
  showToast('⚠️ El administrador se desconectó. Reiniciando tablero en 5s…', 5500);

  setTimeout(function() {
    drawnLocal    = [];
    lastLocal     = null;
    gameStarted   = false;
    gameId        = null;
    lastPhraseKey = null;
    elapsedSec    = 0;
    resetPending  = false;
    if (clockJob) { clearInterval(clockJob); clockJob = null; }
    stopAudio();

    initGridReset();
    const bn = document.getElementById('big-number');
    if (bn) bn.textContent = '?';
    const wd = document.getElementById('words-display');
    if (wd) wd.textContent = '—';
    const gt = document.getElementById('group-tag');
    if (gt) gt.textContent = '';
    const rn = document.getElementById('recent-nums');
    if (rn) rn.innerHTML = '';
    const lb = document.getElementById('last-big');
    if (lb) { lb.textContent = '—'; lb.style.color = ''; }
    document.getElementById('timer').textContent = '⏱ 00:00';
    updateStats(0, 90);
    updateStatusMsg(0, 90);

    adminWasOnline = true;
    if (statusEl) statusEl.textContent = '🔄 Esperando al administrador…';
    showToast('🔄 Tablero reiniciado. Esperando nuevo sorteo…');
  }, 5000);
}

// ── SYNC CON EL SERVIDOR ──────────────────────────
async function syncState() {
  if (resetPending) return; // don't sync while resetting

  try {
    const res  = await fetch('/api/state');
    const data = await res.json();
    const serverDrawn  = data.drawn || [];
    const serverGameId = data.game_id;

    // Update sync badge
    const statusEl = document.getElementById('sync-status');
    if (statusEl && adminWasOnline) {
      statusEl.innerHTML = '✅ Sincronizado';
    }

    // ── Admin timeout detection ──
    // Only alert if game was in progress (avoid false positives at startup)
    if (gameStarted && data.admin_online === false) {
      handleAdminOffline();
      return;
    }

    // ── New game ID = admin reset the game ──
    if (gameId && serverGameId && gameId !== serverGameId && drawnLocal.length > 0) {
      drawnLocal    = [];
      lastLocal     = null;
      gameStarted   = false;
      lastPhraseKey = null;
      elapsedSec    = 0;
      if (clockJob) { clearInterval(clockJob); clockJob = null; }
      stopAudio();
      initGridReset();
      document.getElementById('timer').textContent = '⏱ 00:00';
      const lb = document.getElementById('last-big');
      if (lb) { lb.textContent = '—'; lb.style.color = ''; }
      showToast('🔄 El juego fue reiniciado por el administrador');
    }
    gameId = serverGameId;

    // No change → skip
    if (serverDrawn.length === drawnLocal.length) return;

    const newNums = serverDrawn.filter(function(n) { return !drawnLocal.includes(n); });
    drawnLocal = serverDrawn;

    if (serverDrawn.length > 0 && !gameStarted) {
      gameStarted = true;
      startClock();
      updateStatusMsg(serverDrawn.length, data.remaining);
    }

    // Re-mark all (recovery after page reload)
    drawnLocal.forEach(function(n) { markCell(n, false); });

    if (newNums.length > 0) {
      const latest = newNums[newNums.length - 1];
      markCell(latest, true);
      updateDisplay(latest);
      updateStatusMsg(serverDrawn.length, data.remaining);

      // ── Player audio ──
      const phrase   = data.last_phrase;
      const pKey     = serverDrawn.length; // unique key per draw
      if (phrase && pKey !== lastPhraseKey) {
        lastPhraseKey = pKey;
        // Use player's own selected voice (or fall back to admin voice)
        const pVoice = (document.getElementById('player-voice-select') || {}).value || data.last_voice || 'es-PE-CamilaNeural';
        playPhrase(phrase, pVoice);
      }
    }

    updateRecent();
    updateStats(serverDrawn.length, data.remaining);
    updateMyCartillaAutoMark();

    if (data.remaining === 0 && serverDrawn.length === 90) {
      showGameOver();
    }

  } catch(e) {
    const statusEl = document.getElementById('sync-status');
    if (statusEl) statusEl.innerHTML = '<span style="color:var(--warning)">⚠️ Sin conexión…</span>';
  }
}

// ── DISPLAY ───────────────────────────────────────
function updateDisplay(num) {
  const g           = Math.min(Math.floor((num - 1) / 10), 8);
  const fg          = GROUP_COLORS[g].fg;
  const ball        = document.getElementById('ball');
  const bigNum      = document.getElementById('big-number');

  const ballMids  = ['#1a4a7a','#7a6010','#7a2020','#7a3810','#0f5a28','#4a1a6a','#0a5a4a','#1a3a5a','#2a3540'];
  const ballDarks = ['#0a1e2e','#2e2504','#3d0a08','#3d1800','#0a2e16','#22083d','#073832','#0a1f2e','#151d23'];

  ball.style.background = 'radial-gradient(circle at 35% 32%, #ffffff44 0%, ' +
    ballMids[g] + '99 30%, ' + ballDarks[g] + ' 70%, #020508 100%)';
  ball.style.boxShadow = '0 0 0 3px ' + fg + '55, 0 0 35px ' + fg + '33, ' +
    'inset 0 -8px 20px rgba(0,0,0,0.7), inset 0 8px 16px rgba(255,255,255,0.08)';

  ball.classList.remove('reveal');
  void ball.offsetWidth;
  ball.classList.add('reveal');
  setTimeout(function() { ball.classList.remove('reveal'); }, 600);

  bigNum.textContent      = num;
  bigNum.style.color      = fg;
  bigNum.style.textShadow = '0 0 20px ' + fg + '88, 0 2px 4px rgba(0,0,0,0.8)';

  const gt = document.getElementById('group-tag');
  if (gt) { gt.textContent = 'Grupo ' + GROUP_LABELS[g]; gt.style.color = fg; }

  const lb = document.getElementById('last-big');
  if (lb) { lb.textContent = num; lb.style.color = fg; }

  lastLocal = num;
}

function markCell(num, animate) {
  const cell = document.getElementById('cell-' + num);
  if (!cell) return;
  const g  = Math.min(Math.floor((num - 1) / 10), 8);
  const fg = GROUP_COLORS[g].fg;
  const bg = GROUP_COLORS[g].bg;
  cell.classList.add('drawn');
  if (animate) cell.classList.add('just-drawn');
  cell.style.color       = fg;
  cell.style.background  = bg;
  cell.style.borderColor = fg;
  if (animate) setTimeout(function() { cell.classList.remove('just-drawn'); }, 600);
}

function updateRecent() {
  const strip = document.getElementById('recent-nums');
  if (!strip) return;
  strip.innerHTML = '';
  drawnLocal.slice(-18).reverse().forEach(function(n) {
    const g  = Math.min(Math.floor((n - 1) / 10), 8);
    const el = document.createElement('div');
    el.className   = 'recent-num';
    el.textContent = n;
    el.style.color = GROUP_COLORS[g].fg;
    strip.appendChild(el);
  });
}

function updateStats(count, remaining) {
  document.getElementById('stat-drawn').textContent = count;
  document.getElementById('stat-rem').textContent   = remaining;
  const pct = Math.round((count / 90) * 100);
  const prog = document.getElementById('progress');
  if (prog) prog.style.width = pct + '%';
  const pctEl = document.getElementById('stat-pct');
  if (pctEl) pctEl.textContent = pct + '%';
}

function updateStatusMsg(count, remaining) {
  const el = document.getElementById('game-status-msg');
  if (!el) return;
  if (count === 0) {
    el.textContent = 'Esperando que el administrador inicie el sorteo…';
  } else if (remaining === 0) {
    el.innerHTML = '<strong style="color:var(--accent)">🎉 ¡Juego completo!</strong><br>Se sortearon las 90 bolillas.';
  } else {
    el.innerHTML = 'Bolillas sorteadas: <strong style="color:var(--accent)">' + count + '</strong><br>' +
      'Quedan: <strong style="color:var(--warning)">' + remaining + '</strong> bolillas';
  }
}

// ── RELOJ ─────────────────────────────────────────
function startClock() {
  elapsedSec = 0;
  if (clockJob) clearInterval(clockJob);
  clockJob = setInterval(function() {
    elapsedSec++;
    const h  = Math.floor(elapsedSec / 3600);
    const m  = Math.floor((elapsedSec % 3600) / 60);
    const s  = elapsedSec % 60;
    const mm = String(m).padStart(2, '0');
    const ss = String(s).padStart(2, '0');
    const timerEl = document.getElementById('timer');
    if (timerEl) timerEl.textContent = h ? ('⏱ ' + h + ':' + mm + ':' + ss) : ('⏱ ' + mm + ':' + ss);
  }, 1000);
}

// ── GAME OVER ─────────────────────────────────────
function showGameOver() {
  const timerEl = document.getElementById('timer');
  const timer   = timerEl ? timerEl.textContent.replace('⏱ ', '') : '—';
  const infoEl  = document.getElementById('gameover-info');
  if (infoEl) infoEl.textContent = '¡Se sortearon las 90 bolillas en ' + timer + '!';
  const go = document.getElementById('gameover');
  if (go) go.classList.add('show');
  launchConfetti();
}

function hideGameOver() {
  const go = document.getElementById('gameover');
  if (go) go.classList.remove('show');
}

function launchConfetti() {
  const colors = ['#00e5b4','#f6c343','#e74c3c','#2f80ed','#a569bd','#58d68d'];
  for (let i = 0; i < 80; i++) {
    const c = document.createElement('div');
    c.className = 'confetti-piece';
    c.style.left              = (Math.random() * 100) + 'vw';
    c.style.background        = colors[Math.floor(Math.random() * colors.length)];
    c.style.animationDuration = (Math.random() * 2 + 2) + 's';
    c.style.animationDelay    = (Math.random() * 1.5) + 's';
    c.style.width  = (Math.random() * 8 + 4) + 'px';
    c.style.height = (Math.random() * 8 + 4) + 'px';
    document.body.appendChild(c);
    setTimeout(function() { c.remove(); }, 5000);
  }
}

// ── CARTILLA UI ───────────────────────────────────
function getMyCartillasFromStorage() {
  try { return JSON.parse(localStorage.getItem('my_cartillas') || '[]') || []; }
  catch(e) { return []; }
}

function populateMyCartillaSelect() {
  const sel = document.getElementById('my-cartilla-select');
  if (!sel) return;
  const arr = getMyCartillasFromStorage();
  sel.innerHTML = '';
  const opt0 = document.createElement('option');
  opt0.value = '';
  opt0.textContent = arr.length ? '— Selecciona tu cartilla —' : 'Aún no tienes cartillas en este dispositivo';
  sel.appendChild(opt0);
  arr.forEach(function(cid) {
    const o = document.createElement('option');
    o.value = cid; o.textContent = 'Cartilla ' + cid;
    sel.appendChild(o);
  });
  const last = localStorage.getItem('active_cartilla') || '';
  if (last && arr.includes(last)) sel.value = last;
}

function renderMyCartilla(grid, drawnSet) {
  const wrap = document.getElementById('my-cartilla-grid');
  if (!wrap) return;
  wrap.innerHTML = '';

  // Show the mini wrapper
  const miniWrap = document.getElementById('cartilla-mini-wrap');
  if (miniWrap) miniWrap.style.display = 'block';

  // Build column labels once
  const colsEl = document.getElementById('cartilla-mini-cols');
  if (colsEl && !colsEl.dataset.built) {
    colsEl.dataset.built = '1';
    colsEl.innerHTML = '';
    var colLabels = ['1-9','10-19','20-29','30-39','40-49','50-59','60-69','70-79','80-90'];
    colLabels.forEach(function(lbl, ci) {
      var d = document.createElement('div');
      d.className   = 'cartilla-mini-col-label';
      d.textContent = lbl;
      d.style.color      = GROUP_COLORS[ci].fg;
      d.style.background = GROUP_COLORS[ci].bg;
      colsEl.appendChild(d);
    });
  }
  for (let ri = 0; ri < 3; ri++) {
    for (let ci = 0; ci < 9; ci++) {
      const num  = grid[ri][ci];
      const g    = GROUP_COLORS[ci];
      const cell = document.createElement('div');
      cell.className = 'c-cell';
      if (num === null || num === undefined) {
        cell.classList.add('empty'); cell.textContent = '·';
      } else {
        cell.classList.add('filled');
        if (drawnSet && drawnSet.has(num)) {
          cell.classList.add('marked');
          cell.style.borderColor = g.fg;
          const s = document.createElement('span');
          s.textContent = num; s.style.background = g.fg;
          cell.appendChild(s);
        } else {
          cell.textContent = num; cell.style.color = g.fg + '44';
        }
      }
      wrap.appendChild(cell);
    }
  }
}

async function loadSelectedCartilla() {
  const sel = document.getElementById('my-cartilla-select');
  const cid = (sel ? sel.value : '').trim().toUpperCase();
  if (!cid) { showToast('🎴 Selecciona una cartilla'); return; }
  myCartillaId = cid;
  localStorage.setItem('active_cartilla', cid);
  myBingoFired = false;
  try {
    const res  = await fetch('/api/cartilla/' + cid);
    const data = await res.json();
    if (!res.ok) { showToast('❌ No se encontró esa cartilla'); return; }
    myCartilla = data;
    const meta = document.getElementById('my-cartilla-meta');
    if (meta) meta.innerHTML = 'ID: <strong style="color:var(--text)">' + data.id + '</strong>';
    updateMyCartillaAutoMark(true);
    showToast('✅ Cartilla ' + cid + ' cargada');
    try {
      if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
    } catch(e) {}
  } catch(e) { showToast('❌ Error al cargar cartilla'); }
}

function isMyCartillaBingo(drawnSet) {
  if (!myCartilla || !myCartilla.grid) return false;
  const nums = [];
  for (const row of myCartilla.grid) for (const n of row) if (n !== null && n !== undefined) nums.push(n);
  return nums.length && nums.every(function(n) { return drawnSet.has(n); });
}

function playWinAlert() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = 'sine'; o.frequency.value = 880;
    o.connect(g); g.connect(ctx.destination);
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.4, ctx.currentTime + 0.05);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 1.2);
    o.start(); o.stop(ctx.currentTime + 1.25);
    setTimeout(function() { ctx.close(); }, 1400);
  } catch(e) {}
  try { if (navigator.vibrate) navigator.vibrate([200, 100, 200, 100, 400]); } catch(e) {}
}

function showWinNotification(title, body) {
  try {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'granted') new Notification(title, { body: body });
  } catch(e) {}
}

async function claimWinnerOnce() {
  if (!myCartillaId || !gameId) return;
  const key = 'winner_claimed_' + gameId + '_' + myCartillaId;
  if (localStorage.getItem(key) === '1') return;
  try {
    const res  = await fetch('/api/winner/claim', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cid: myCartillaId }),
    });
    const data = await res.json();
    if (res.ok && data.ok) { localStorage.setItem(key, '1'); return data; }
  } catch(e) {}
  return null;
}

function updateMyCartillaAutoMark(force) {
  const status = document.getElementById('my-cartilla-status');
  const btnBuy = document.getElementById('btn-go-cartillas');
  if (btnBuy) { btnBuy.disabled = gameStarted; btnBuy.textContent = gameStarted ? 'Ver/Ya compré' : 'Comprar'; }
  if (!myCartilla || !myCartilla.grid) {
    if (status) status.textContent = gameStarted
      ? '💡 El juego ya empezó. Carga tu cartilla para marcar automáticamente.'
      : '💡 Puedes comprar/generar tu cartilla antes de que empiece el juego.';
    return;
  }
  const drawnSet = new Set(drawnLocal);
  renderMyCartilla(myCartilla.grid, drawnSet);
  let marked = 0;
  for (const row of myCartilla.grid) for (const n of row) if (n !== null && n !== undefined && drawnSet.has(n)) marked++;
  if (status) status.innerHTML = 'Marcadas: <strong style="color:var(--accent)">' + marked + ' / 15</strong>';
  if (!myBingoFired && isMyCartillaBingo(drawnSet)) {
    myBingoFired = true;
    playWinAlert();
    showToast('🎉 ¡BINGO!');
    showWinNotification('🎉 ¡BINGO!', 'Cartilla ' + myCartillaId + ' — ¡Felicidades!');
    claimWinnerOnce().then(function(r) { if (r && r.sms_sent) showToast('📩 Te enviamos un SMS'); });
  }
}

function initMyCartillaUI() {
  populateMyCartillaSelect();
  const btn = document.getElementById('btn-load-cartilla');
  if (btn) btn.addEventListener('click', loadSelectedCartilla);
  const sel = document.getElementById('my-cartilla-select');
  if (sel) sel.addEventListener('change', function() {
    const cid = (sel.value || '').trim();
    if (cid) loadSelectedCartilla();
  });
  window.addEventListener('focus', populateMyCartillaSelect);
}

// ── INIT ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  const urlEl = document.getElementById('server-url');
  if (urlEl) urlEl.textContent = window.location.href;

  initGrid();
  initMyCartillaUI();

  syncState();
  setInterval(syncState, 3000);
});