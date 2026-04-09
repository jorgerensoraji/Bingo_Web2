/* ═══════════════════════════════════════════════════════
   BINGO PRO — game_player.js  v7.2
   Made by Renso Ramirez  |  Enhanced v7.2

   Base: v5.0 original (todas las funciones preservadas)
   v6.0 agrega:
   ✅ Botón ¡RECLAMAR BINGO! aparece automáticamente
   ✅ Botón ¡RECLAMAR LÍNEA! con monto del premio
   ✅ Alerta "¡Falta 1!" pulsante + voz
   ✅ Pozo de premios visible en tiempo real
   ✅ Ganadores en live en el panel de estado
   v7.1 agrega:
   ✅ Hook countdown banner (_v71_cdHook)
   v7.2 agrega:
   ✅ Pozo se divide entre todos los ganadores (empate)
   ✅ Jugador puede ganar LÍNEA y BINGO en el mismo juego
   ✅ Mensaje de empate en overlay y toast
═══════════════════════════════════════════════════════ */

// ── BINGO 75 column colours (B I N G O) ──────────────
const BINGO75_COLORS = [
  { fg: '#3b82f6', bg: '#060f20', letter: 'B' },
  { fg: '#f59e0b', bg: '#1a1000', letter: 'I' },
  { fg: '#ef4444', bg: '#1a0505', letter: 'N' },
  { fg: '#10b981', bg: '#051a10', letter: 'G' },
  { fg: '#a855f7', bg: '#130820', letter: 'O' },
];

// ── Game mode: always Bingo 75 B-I-N-G-O ─────────────
var isBingo75    = true;
var gameBalls    = 75;

// ── ESTADO ────────────────────────────────────────────
let drawnLocal     = [];
let lastLocal      = null;
let clockJob       = null;
let elapsedSec     = 0;
let gameStarted    = false;
let gameId         = null;
let lastPhraseKey  = null;
let soundEnabled   = false;
let currentAudio   = null;  // kept for compat
let testAudio      = null;
let _speakAbort    = null;  // AbortController for in-flight TTS fetch
let _audioEl       = null;  // single reusable <audio> element (mobile-safe)
let adminWasOnline = true;
let resetPending   = false;

// ── SESIÓN ACTIVA ─────────────────────────────────────
let activeSessionId  = null;   // se actualiza desde /api/state
let adminWhatsapp    = '';     // número de WhatsApp del admin (desde /api/state)

// ── MI CARTILLA ───────────────────────────────────────
let myCartilla    = null;
let myCartillas   = [];      // array of loaded cartilla objects
let myBingoFired  = false;
let claimedBingo  = false;   // v6 — reclamo de bingo
let claimedLinea  = false;   // v6 — reclamo de línea
let almostSpoken  = false;   // v6 — evitar repetir "¡Falta 1!"
let cartillaStates = {};     // per-cartilla claim/alert state

// ── TOAST ─────────────────────────────────────────────
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

// ── NAVIGATION ────────────────────────────────────────
function goToGame() { location.href = '/'; }

// ── INIT GRID ─────────────────────────────────────────
function initGridForMode() {
  const headers = document.getElementById('group-headers');
  const grid    = document.getElementById('num-grid');
  if (!headers || !grid) return;
  headers.innerHTML = '';
  grid.innerHTML    = '';

  grid.style.gridTemplateColumns = 'repeat(5,1fr)';
  grid.style.gridTemplateRows   = 'repeat(15,1fr)';
  BINGO75_COLORS.forEach(function(col) {
    const h = document.createElement('div');
    h.className = 'group-header';
    h.textContent = col.letter;
    h.style.color      = col.fg;
    h.style.background = col.bg;
    h.style.fontSize   = '1rem';
    h.style.fontWeight = '900';
    headers.appendChild(h);
  });
  for (let col = 0; col < 5; col++) {
    for (let row = 0; row < 15; row++) {
      const num  = col * 15 + row + 1;
      const cell = document.createElement('div');
      cell.className        = 'num-cell';
      cell.id               = 'cell-' + num;
      cell.textContent      = num;
      cell.style.gridColumn = col + 1;
      cell.style.gridRow    = row + 1;
      grid.appendChild(cell);
    }
  }
}

function initGrid() { initGridForMode(); }

function initGridReset() {
  for (let n = 1; n <= gameBalls; n++) {
    const cell = document.getElementById('cell-' + n);
    if (!cell) continue;
    cell.classList.remove('drawn', 'just-drawn');
    cell.style.color       = '';
    cell.style.background  = '';
    cell.style.borderColor = '';
  }
}

// ── AUDIO ─────────────────────────────────────────────
// Single reusable <audio> element — avoids iOS audio-session resets
// that happen when you create new Audio() objects repeatedly.
function _getAudioEl() {
  if (!_audioEl) {
    _audioEl = document.createElement('audio');
    _audioEl.preload = 'none';
    document.body.appendChild(_audioEl);
  }
  return _audioEl;
}

function stopAudio() {
  // Cancel any pending TTS fetch first
  if (_speakAbort) { _speakAbort.abort(); _speakAbort = null; }
  // Pause without clearing src — clearing src on iOS resets the audio session
  var a = _getAudioEl();
  a.pause();
  currentAudio = null;
  setHostTalking(false);
}

function stopTestAudio() {
  if (testAudio) {
    testAudio.pause();
    testAudio = null;
  }
}

/* ── Bingo Bot host character ── */
function setHostTalking(talking, ballText) {
  var host = document.getElementById('bingo-host');
  if (!host) return;
  if (talking) {
    host.classList.add('talking');
  } else {
    host.classList.remove('talking');
  }
  if (ballText !== undefined) {
    var bubble = document.getElementById('host-bubble-text');
    if (bubble) bubble.textContent = ballText;
  }
}

function playPhrase(text, voice) {
  if (!soundEnabled || !text) return;

  // Abort any in-flight TTS fetch so we never play two overlapping phrases
  if (_speakAbort) { _speakAbort.abort(); }
  _speakAbort = new AbortController();
  var ctrl = _speakAbort;

  voice = voice || 'es-PE-CamilaNeural';

  fetch('/api/speak', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ text: text, voice: voice }),
    signal:  ctrl.signal,
  })
  .then(function(r) {
    if (ctrl.signal.aborted) return null;
    if (!r.ok) throw new Error('speak HTTP ' + r.status);
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('audio')) throw new Error('speak returned non-audio: ' + ct);
    return r.blob();
  })
  .then(function(blob) {
    if (!blob || blob.size < 100 || ctrl.signal.aborted) return;
    var prevUrl = _audioEl ? _audioEl.src : '';
    var a = _getAudioEl();
    var url = URL.createObjectURL(blob);
    a.pause();
    a.src = url;
    a.load();   // required on iOS to apply new src
    setHostTalking(true);
    var p = a.play();
    if (p && p.catch) {
      p.catch(function(e) {
        if (e.name !== 'AbortError') {
          console.warn('Audio play blocked:', e);
          showToast('🔇 Haz clic en la página para activar sonido');
          setHostTalking(false);
        }
      });
    }
    a.onended = function() { URL.revokeObjectURL(url); setHostTalking(false); };
    if (prevUrl && prevUrl.startsWith('blob:')) URL.revokeObjectURL(prevUrl);
    _speakAbort = null;
    currentAudio = a;
  })
  .catch(function(e) {
    if (e.name !== 'AbortError') console.error('playPhrase error:', e);
  });
}

function toggleSound() {
  if (soundEnabled) { disableSound(); } else { enableSound(); }
}

function disableSound() {
  soundEnabled = false;
  stopAudio();
  const btn = document.getElementById('btn-sound');
  if (btn) {
    btn.textContent       = '🔈 Activar sonido';
    btn.style.background  = '';
    btn.style.color       = '';
    btn.style.borderColor = '';
  }
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
    var a = _getAudioEl();
    var url = URL.createObjectURL(blob);
    a.pause();
    a.src = url;
    a.load();
    var p = a.play();
    if (p && p.catch) p.catch(function(e) {
      if (e.name === 'NotAllowedError') {
        showToast('❌ El navegador bloqueó el audio. Intenta de nuevo.');
        soundEnabled = false;
        if (btn) { btn.textContent = '🔈 Activar sonido'; btn.style.background = ''; btn.style.color = ''; btn.style.borderColor = ''; }
      }
    });
    a.onended = function() {
      URL.revokeObjectURL(url);
      showToast('✅ Sonido OK — escucharás cada bolilla');
    };
  })
  .catch(function(e) {
    console.error('Test audio error:', e);
    showToast('❌ Error de audio: ' + e.message);
  });
}

// ── v6: Reproducir alerta de falta 1 ─────────────────
function playAlmostAlert(voice) {
  // Routed through playPhrase so AbortController logic applies
  playPhrase('¡Falta uno para el bingo!', voice || 'es-PE-CamilaNeural');
}

// ── ADMIN OFFLINE HANDLING ────────────────────────────
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
    claimedBingo  = false;
    claimedLinea  = false;
    almostSpoken  = false;
    if (clockJob) { clearInterval(clockJob); clockJob = null; }
    stopAudio();

    initGridReset();
    removeClaimButtons();
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
    updateStats(0, gameBalls);
    updateStatusMsg(0, gameBalls);

    adminWasOnline = true;
    if (statusEl) statusEl.textContent = '🔄 Esperando al administrador…';
    showToast('🔄 Tablero reiniciado. Esperando nuevo sorteo…');
  }, 5000);
}

// ── BANNER GLOBAL DE GANADOR (visible para todos) ────────────────────────────
var _lastBannerKey = '';

function showGlobalWinnerBanner(winners, lineaWinners, isPaused) {
  var hasBingo = winners && winners.length > 0;
  var hasLinea = lineaWinners && lineaWinners.length > 0;

  var key = (hasBingo ? winners.map(function(w){return w.id;}).join(',') : '') +
            '|' + (hasLinea ? lineaWinners.map(function(w){return w.id;}).join(',') : '');

  var el = document.getElementById('global-winner-banner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'global-winner-banner';
    el.style.cssText = [
      'position:fixed;top:0;left:0;right:0;z-index:800;',
      'display:none;flex-direction:column;gap:0;',
    ].join('');
    document.body.appendChild(el);
  }

  if (!hasBingo && !hasLinea) {
    el.style.display = 'none';
    _lastBannerKey = '';
    return;
  }

  el.style.display = 'flex';

  // Only rebuild HTML when winners change
  if (key === _lastBannerKey) return;
  _lastBannerKey = key;

  var html = '';

  // LÍNEA banner (always on top, game continues)
  if (hasLinea) {
    var lw = lineaWinners[0];
    var lp = Number(lw.linea_prize || 0).toFixed(2);
    var lnames = lineaWinners.map(function(w){ return (w.nombre||w.id); }).join(' & ');
    html += '<div style="background:linear-gradient(90deg,#7a5800,#c49200,#7a5800);' +
      'color:#fff8e0;padding:10px 20px;text-align:center;font-family:\'Outfit\',sans-serif;' +
      'font-size:.92rem;font-weight:700;border-bottom:2px solid #f6c343;">' +
      '⭐ <strong>' + esc(lnames) + '</strong> ganó la LÍNEA — Premio: S/. ' + lp +
      ' &nbsp;|&nbsp; <span style="font-weight:400;font-size:.82rem">El juego continúa ▶</span>' +
      '</div>';
  }

  // BINGO banner (game stopped)
  if (hasBingo) {
    var bw   = winners[0];
    var bp   = Number(bw.prize || 0).toFixed(2);
    var bnames = winners.map(function(w){ return (w.nombre||w.id); }).join(' & ');
    var splitNote = winners.length > 1
      ? ' (Empate — S/. ' + bp + ' c/u)'
      : ' — Premio: S/. ' + bp;
    html += '<div style="background:linear-gradient(90deg,#004d3a,#007a5a,#004d3a);' +
      'color:#d0fff5;padding:14px 20px;text-align:center;font-family:\'Outfit\',sans-serif;' +
      'font-size:1rem;font-weight:700;animation:glowBanner 1.5s ease-in-out infinite;">' +
      '🎉 ¡BINGO! <strong style="color:#00e5b4;font-size:1.15rem">' + esc(bnames) + '</strong>' +
      esc(splitNote) +
      ' &nbsp;|&nbsp; <span style="font-weight:400;font-size:.82rem">Juego detenido ⏸</span>' +
      '</div>';

    // Play winner alert sound once per new winner
    if (soundEnabled) playWinAlert();
    launchConfetti();
  }

  el.innerHTML = html;

  // ── Payment notice for the current player if they won ──
  var myIds = new Set(myCartillas.map(function(c){ return (c.id || c); }));
  var iAmBingoWinner = hasBingo && winners.some(function(w){ return myIds.has(w.id); });
  var iAmLineaWinner = hasLinea && lineaWinners.some(function(w){ return myIds.has(w.id); });

  if (iAmBingoWinner || iAmLineaWinner) {
    var waPhone = (adminWhatsapp || '').replace(/\D/g, '');
    var waLink  = waPhone
      ? 'https://wa.me/' + waPhone + '?text=' + encodeURIComponent('Hola, acabo de ganar el bingo y quiero consultar sobre mi pago.')
      : null;
    var waBtn = waLink
      ? '<a href="' + waLink + '" target="_blank" style="display:inline-block;margin-top:8px;padding:8px 20px;background:#25d366;color:#fff;border-radius:8px;font-weight:900;font-size:.85rem;text-decoration:none;">📲 Contactar por WhatsApp</a>'
      : '';
    var noticeId = 'payment-notice-winner';
    var existing = document.getElementById(noticeId);
    if (!existing) {
      var notice = document.createElement('div');
      notice.id = noticeId;
      notice.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);' +
        'background:linear-gradient(135deg,#004d3a,#006b50);color:#d0fff5;' +
        'border:2px solid #00e5b4;border-radius:16px;padding:16px 22px;' +
        'text-align:center;z-index:900;max-width:90vw;width:360px;' +
        'box-shadow:0 8px 40px rgba(0,229,180,.4);font-family:\'Outfit\',sans-serif;';
      notice.innerHTML =
        '<div style="font-size:1.5rem;margin-bottom:6px">💸</div>' +
        '<div style="font-weight:900;font-size:1rem;margin-bottom:4px">¡Felicidades, ganador!</div>' +
        '<div style="font-size:.82rem;opacity:.9;line-height:1.5">Tu pago será procesado en los próximos <strong>30 minutos</strong>.' +
        (waLink ? '<br>Si no recibes el pago, contáctanos por WhatsApp.' : '') + '</div>' +
        waBtn +
        '<button onclick="document.getElementById(\'' + noticeId + '\').remove()" ' +
        'style="display:block;margin:10px auto 0;background:rgba(255,255,255,.12);border:none;' +
        'color:#d0fff5;padding:5px 16px;border-radius:6px;cursor:pointer;font-size:.76rem;">Cerrar</button>';
      document.body.appendChild(notice);
    }
  }
}

// ── SESIÓN FINALIZADA ─────────────────────────────────
var sessionFinishedShown = false;

function handleSessionFinished() {
  if (sessionFinishedShown) return;
  sessionFinishedShown = true;

  // ── Remove finished-session cartillas from localStorage ──
  // Use activeSessionId + loaded myCartillas to identify which IDs to purge.
  var finishedSid = activeSessionId;
  if (finishedSid) {
    // IDs we know belong to this session (already fetched from server)
    var toRemove = new Set();
    myCartillas.forEach(function(c) {
      if (c.session_id === finishedSid) toRemove.add(c.id);
    });
    try {
      var stored = JSON.parse(localStorage.getItem('my_cartillas') || '[]');
      var remaining = stored.filter(function(e) {
        var cid = (typeof e === 'object' && e !== null) ? e.id : e;
        var sid = (typeof e === 'object' && e !== null) ? (e.session_id || '') : '';
        if (toRemove.has(cid)) return false;          // known from loaded data
        if (sid && sid === finishedSid) return false; // known from localStorage metadata
        return true;
      });
      localStorage.setItem('my_cartillas', JSON.stringify(remaining));
    } catch(err) {}
  }

  // Stop clock and audio
  if (clockJob) { clearInterval(clockJob); clockJob = null; }
  stopAudio();

  // Reset all local game state
  drawnLocal    = [];
  lastLocal     = null;
  gameStarted   = false;
  gameId        = null;
  lastPhraseKey = null;
  elapsedSec    = 0;
  claimedBingo  = false;
  claimedLinea  = false;
  almostSpoken  = false;
  activeSessionId = null;
  myCartillas    = [];
  cartillaStates = {};
  _lastBannerKey = '';
  var bannerEl = document.getElementById('global-winner-banner');
  if (bannerEl) bannerEl.style.display = 'none';
  var psBanner = document.getElementById('player-session-banner');
  if (psBanner) psBanner.style.display = 'none';

  // Reset the drawn-balls grid
  initGridReset();
  removeClaimButtons();

  // Reset display elements
  var bn = document.getElementById('big-number');
  if (bn) { bn.textContent = '?'; bn.style.color = ''; bn.style.fontSize = ''; }
  var wd = document.getElementById('words-display');
  if (wd) wd.textContent = '—';
  var gt = document.getElementById('group-tag');
  if (gt) gt.textContent = '';
  var rn = document.getElementById('recent-nums');
  if (rn) rn.innerHTML = '';
  var lb = document.getElementById('last-big');
  if (lb) { lb.textContent = '—'; lb.style.color = ''; }
  var timer = document.getElementById('timer');
  if (timer) timer.textContent = '⏱ 00:00';

  updateStats(0, gameBalls);

  // Clear cartilla panels
  var wrap = document.getElementById('mis-cartillas-wrap');
  if (wrap) wrap.innerHTML = '';

  // Show finished message in status area
  var statusEl = document.getElementById('game-status-msg');
  if (statusEl) {
    statusEl.innerHTML =
      '<div style="text-align:center;padding:20px 10px">' +
      '<div style="font-size:2rem;margin-bottom:8px">🏁</div>' +
      '<div style="font-family:\'Bebas Neue\',sans-serif;font-size:1.6rem;color:var(--accent);letter-spacing:2px">Sesión Finalizada</div>' +
      '<div style="color:var(--muted);font-size:.83rem;margin-top:8px;line-height:1.6">' +
      'Esta sesión de bingo ha terminado.<br>Gracias por participar. ¡Hasta la próxima!</div>' +
      '</div>';
  }

  // Show banner to buy for next session
  var banner = document.getElementById('banner-comprar');
  if (banner) { banner.style.display = ''; banner.style.opacity = '1'; }

  var syncEl = document.getElementById('sync-status');
  if (syncEl) syncEl.innerHTML = '🏁 Sesión finalizada';

  showToast('🏁 La sesión ha finalizado. ¡Hasta la próxima!', 5000);
  hideGameOver();
}

// ── SYNC CON EL SERVIDOR ──────────────────────────────
async function syncState() {
  if (resetPending) return;

  try {
    const res  = await fetch('/api/state');
    const data = await res.json();

    // v7.1 — actualizar countdown banner si está activo
    if (window._v71_cdHook) window._v71_cdHook(data);

    const serverDrawn  = data.drawn || [];
    const serverGameId = data.game_id;
    if (data.admin_whatsapp) adminWhatsapp = data.admin_whatsapp;

    // Re-filter loaded cartillas if active session changed
    const prevSid = activeSessionId;
    if (data.session_id) activeSessionId = data.session_id;
    if (activeSessionId && activeSessionId !== prevSid && myCartillas.length) {
      var before = myCartillas.length;
      myCartillas = myCartillas.filter(function(c) {
        return !c.session_id || c.session_id === activeSessionId;
      });
      cartillaStates = {};
      if (myCartillas.length < before) {
        var removed = before - myCartillas.length;
        showToast('⚠️ ' + removed + ' cartilla(s) son de otra sesión y fueron desactivadas.', 5000);
        updateMyCartillaAutoMark(true);
      }
    }

    gameBalls = data.bingo_balls || 75;

    const statusEl = document.getElementById('sync-status');
    if (statusEl && adminWasOnline) {
      statusEl.innerHTML = '✅ Sincronizado';
    }

    // Admin timeout detection
    if (gameStarted && data.admin_online === false) {
      handleAdminOffline();
      return;
    }

    // Session finished by admin → reset player UI
    if (data.session_finished) {
      handleSessionFinished();
      return;
    }

    // New game ID = admin reset
    if (gameId && serverGameId && gameId !== serverGameId && drawnLocal.length > 0) {
      drawnLocal    = [];
      lastLocal     = null;
      gameStarted   = false;
      lastPhraseKey = null;
      elapsedSec    = 0;
      claimedBingo  = false;
      claimedLinea  = false;
      almostSpoken  = false;
      if (clockJob) { clearInterval(clockJob); clockJob = null; }
      stopAudio();
      initGridReset();
      removeClaimButtons();
      document.getElementById('timer').textContent = '⏱ 00:00';
      const lb = document.getElementById('last-big');
      if (lb) { lb.textContent = '—'; lb.style.color = ''; }
      showToast('🔄 El juego fue reiniciado por el administrador');
    }
    gameId = serverGameId;

    // No change → skip
    if (serverDrawn.length === drawnLocal.length) {
      updatePrizeDisplay(data);
      updateWinnersDisplay(data.winners || [], data.linea_winners || []);
      showGlobalWinnerBanner(data.winners || [], data.linea_winners || [], data.paused);
      return;
    }

    const newNums = serverDrawn.filter(function(n) { return !drawnLocal.includes(n); });
    drawnLocal = serverDrawn;

    if (serverDrawn.length > 0 && !gameStarted) {
      gameStarted = true;
      startClock();
      updateStatusMsg(serverDrawn.length, data.remaining);
    }

    // Re-mark all (recovery after page reload) + remove from drum silently
    drawnLocal.forEach(function(n) {
      markCell(n, false);
      var b = document.getElementById('dball-' + n);
      if (b && b.parentNode) b.parentNode.removeChild(b);
    });
    updateDrumBallSizes();

    if (newNums.length > 0) {
      const latest = newNums[newNums.length - 1];
      markCell(latest, true);
      updateDisplay(latest);
      updateStatusMsg(serverDrawn.length, data.remaining);

      const phrase = data.last_phrase;
      const pKey   = serverDrawn.length;
      if (phrase && pKey !== lastPhraseKey) {
        lastPhraseKey = pKey;
        const pVoice = (document.getElementById('player-voice-select') || {}).value
                    || data.last_voice
                    || 'es-PE-CamilaNeural';
        playPhrase(phrase, pVoice);
      }
    }

    updateRecent();
    updateStats(serverDrawn.length, data.remaining);
    updatePrizeDisplay(data);
    updateWinnersDisplay(data.winners || [], data.linea_winners || []);
    updateMyCartillaAutoMark();
    showGlobalWinnerBanner(data.winners || [], data.linea_winners || [], data.paused);

    if (data.remaining === 0 && serverDrawn.length === gameBalls) {
      showGameOver();
    }

  } catch(e) {
    const statusEl = document.getElementById('sync-status');
    if (statusEl) statusEl.innerHTML = '<span style="color:var(--warning)">⚠️ Sin conexión…</span>';
  }
}

// ── DRUM (bolillero) ──────────────────────────────────
var _drumInited = false;

function initDrum() {
  var drum = document.getElementById('ball-drum');
  if (!drum) return;
  drum.querySelectorAll('.drum-ball').forEach(function(b){ b.parentNode.removeChild(b); });
  _drumInited = true;

  // 5 rings × 15 balls = 75 (B-I-N-G-O)
  var radii  = [16, 25, 35, 46, 58];
  var speeds = [4.2, 5.6, 4.8, 6.3, 5.1];
  for (var g = 0; g < 5; g++) {
    var col    = BINGO75_COLORS[g].fg;
    var radius = radii[g];
    var spd    = speeds[g];
    for (var j = 0; j < 15; j++) {
      var num   = g * 15 + j + 1;
      var delay = -(j * spd / 15).toFixed(2);
      var b     = document.createElement('div');
      b.className = 'drum-ball';
      b.id        = 'dball-' + num;
      b.style.cssText = '--sz:10px;--r:' + radius + 'px;--spd:' + spd + 's;--dly:' + delay + 's;--col:' + col + ';';
      drum.appendChild(b);
    }
  }
}

function removeDrumBall(num) {
  var b = document.getElementById('dball-' + num);
  if (!b) return;
  b.style.transition = 'opacity 0.35s, transform 0.35s';
  b.style.opacity    = '0';
  b.style.transform  = b.style.transform + ' scale(0)';
  setTimeout(function() {
    if (b.parentNode) b.parentNode.removeChild(b);
    updateDrumBallSizes();
  }, 420);
}

function updateDrumBallSizes() {
  var balls = Array.from(document.querySelectorAll('#ball-drum .drum-ball'));
  var count = balls.length;
  if (count === 0) return;

  // Size thresholds: fewer balls → bigger → numbers visible
  var sz, showNum;
  if      (count <= 5)  { sz = 38; showNum = true; }
  else if (count <= 10) { sz = 30; showNum = true; }
  else if (count <= 18) { sz = 22; showNum = true; }
  else if (count <= 35) { sz = 16; showNum = true; }
  else                  { sz = 10; showNum = false; }

  // Recalculate rings so balls fit inside the 150px drum (inner radius 75px)
  var half    = sz / 2;
  var maxR    = 68 - half;
  var minR    = half + 5;
  var numRings = count <= 5 ? 1 : count <= 14 ? 2 : count <= 36 ? 3 : 5;
  numRings     = Math.min(numRings, count);
  var perRing  = Math.ceil(count / numRings);

  balls.forEach(function(b, i) {
    var ring        = Math.floor(i / perRing);
    var posInRing   = i % perRing;
    var ballsInRing = Math.min(perRing, count - ring * perRing);
    var r   = numRings === 1
      ? Math.round((minR + maxR) / 2)
      : Math.round(minR + (ring / (numRings - 1)) * (maxR - minR));
    var spd = parseFloat((4.2 + ring * 1.4 + (i % 3) * 0.25).toFixed(2));
    var dly = parseFloat((-posInRing * spd / ballsInRing).toFixed(2));

    // Update CSS custom properties (width/height/margin derive from --sz in CSS)
    b.style.setProperty('--sz', sz + 'px');
    b.style.boxShadow = '0 0 ' + (sz * 0.55 | 0) + 'px var(--col)';

    // Restart animation with new radius + speed
    b.style.animation = 'none';
    b.getBoundingClientRect(); // force reflow
    b.style.animation = 'drumOrbit ' + spd + 's linear ' + dly + 's infinite';

    // Show number when big enough
    var num = parseInt(b.id.replace('dball-', ''), 10);
    if (showNum) {
      b.textContent          = num;
      b.style.fontSize       = Math.round(sz * 0.50) + 'px';
      b.style.display        = 'flex';
      b.style.alignItems     = 'center';
      b.style.justifyContent = 'center';
      b.style.fontFamily     = "'Bebas Neue', sans-serif";
      b.style.fontWeight     = '900';
      b.style.color          = '#fff';
      b.style.textShadow     = '0 1px 3px rgba(0,0,0,.8)';
    } else {
      b.textContent  = '';
      b.style.display = '';
    }
  });
}

function launchBall(num, color) {
  var drum     = document.getElementById('ball-drum');
  var mainBall = document.getElementById('ball');
  if (!drum || !mainBall) return;

  var fromR = drum.getBoundingClientRect();
  var toR   = mainBall.getBoundingClientRect();

  var sz = 28;
  var startX = fromR.left + fromR.width  / 2 - sz / 2;
  var startY = fromR.top  + fromR.height / 2 - sz / 2;
  var endDX  = (toR.left + toR.width  / 2) - (fromR.left + fromR.width  / 2);
  var endDY  = (toR.top  + toR.height / 2) - (fromR.top  + fromR.height / 2);
  var targetScale = (toR.width / sz) * 0.92;

  var fly = document.createElement('div');
  fly.className = 'fly-ball';
  fly.textContent = num;
  fly.style.cssText =
    'width:' + sz + 'px;height:' + sz + 'px;' +
    'left:' + startX + 'px;top:' + startY + 'px;' +
    'font-size:' + Math.round(sz * 0.58) + 'px;' +
    'background:radial-gradient(circle at 35% 30%,#ffffff55 0%,' + color + '99 45%,#020508 100%);' +
    'color:' + color + ';' +
    'box-shadow:0 0 10px ' + color + '99;';
  document.body.appendChild(fly);

  // Trigger reflow then animate
  fly.getBoundingClientRect();
  fly.style.transition = 'transform 0.55s cubic-bezier(0.34,1.20,0.64,1), opacity 0.12s ease 0.43s';
  fly.style.transform  = 'translate(' + endDX + 'px,' + endDY + 'px) scale(' + targetScale + ')';
  fly.style.opacity    = '0';

  setTimeout(function() { if (fly.parentNode) fly.parentNode.removeChild(fly); }, 680);
}

// ── DISPLAY ───────────────────────────────────────────
function getNumColor(num) {
  return BINGO75_COLORS[Math.min(Math.floor((num-1)/15),4)].fg;
}
function getNumLabel(num) {
  return BINGO75_COLORS[Math.min(Math.floor((num-1)/15),4)].letter + '-' + num;
}

function updateDisplay(num) {
  const fg      = getNumColor(num);
  const ball    = document.getElementById('ball');
  const bigNum  = document.getElementById('big-number');

  const g = Math.min(Math.floor((num-1)/15), 4);

  const ballMids  = ['#1a4a7a','#7a6010','#7a2020','#0f5a28','#4a1a6a'];
  const ballDarks = ['#0a1e2e','#2e2504','#3d0a08','#0a2e16','#22083d'];

  // Remove the ball from the drum and launch it to the display
  removeDrumBall(num);
  launchBall(num, fg);

  ball.style.background = 'radial-gradient(circle at 35% 32%, #ffffff44 0%, ' +
    ballMids[g] + '99 30%, ' + ballDarks[g] + ' 70%, #020508 100%)';
  ball.style.boxShadow = '0 0 0 3px ' + fg + '55, 0 0 35px ' + fg + '33, ' +
    'inset 0 -8px 20px rgba(0,0,0,0.7), inset 0 8px 16px rgba(255,255,255,0.08)';

  // Delay reveal slightly so the fly animation arrives first
  setTimeout(function() {
    ball.classList.remove('reveal');
    void ball.offsetWidth;
    ball.classList.add('reveal');
    setTimeout(function() { ball.classList.remove('reveal'); }, 600);
  }, 420);

  var numLabel = getNumLabel(num);
  bigNum.textContent      = numLabel;
  bigNum.style.color      = fg;
  bigNum.style.textShadow = '0 0 20px ' + fg + '88, 0 2px 4px rgba(0,0,0,0.8)';
  // Shrink font slightly for letter-prefixed numbers (B-12)
  bigNum.style.fontSize   = 'clamp(1.8rem,4vw,3.2rem)';
  // Update host speech bubble
  setHostTalking(false, numLabel);

  const gt = document.getElementById('group-tag');
  if (gt) {
    gt.textContent = BINGO75_COLORS[g].letter + '  (' + (g*15+1) + '–' + (g*15+15) + ')';
    gt.style.color = fg;
  }

  const lb = document.getElementById('last-big');
  if (lb) { lb.textContent = num; lb.style.color = fg; }

  lastLocal = num;
}

function markCell(num, animate) {
  const cell = document.getElementById('cell-' + num);
  if (!cell) return;
  const col = Math.min(Math.floor((num - 1) / 15), 4);
  const fg  = BINGO75_COLORS[col].fg;
  const bg  = BINGO75_COLORS[col].bg;
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
    const fg = BINGO75_COLORS[Math.min(Math.floor((n-1)/15),4)].fg;
    const el = document.createElement('div');
    el.className   = 'recent-num';
    el.textContent = n;
    el.style.color = fg;
    strip.appendChild(el);
  });
}

function updateStats(count, remaining) {
  document.getElementById('stat-drawn').textContent = count;
  document.getElementById('stat-rem').textContent   = remaining;
  const pct  = Math.round((count / gameBalls) * 100);
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
    el.innerHTML = '<strong style="color:var(--accent)">🎉 ¡Juego completo!</strong><br>Se sortearon las ' + gameBalls + ' bolillas.';
  } else {
    el.innerHTML = 'Bolillas sorteadas: <strong style="color:var(--accent)">' + count + '</strong><br>' +
      'Quedan: <strong style="color:var(--warning)">' + remaining + '</strong> bolillas';
  }
}

// ── v6: Mostrar pozo en tiempo real ──────────────────
function updatePrizeDisplay(data) {
  const el = document.getElementById('game-status-msg');
  if (!el || !gameStarted) return;
  const pool  = data.prize_pool  || 0;
  const linea = data.linea_pool  || 0;
  const nW    = data.winners_count || 0;
  // Agregar línea de pozo al panel si no está ya
  let prizeEl = document.getElementById('prize-pool-display');
  if (!prizeEl) {
    prizeEl = document.createElement('div');
    prizeEl.id = 'prize-pool-display';
    prizeEl.style.cssText = 'margin-top:10px;font-size:.82rem;border-top:1px solid var(--border);padding-top:8px';
    el.parentNode.insertBefore(prizeEl, el.nextSibling);
  }
  const splitNote = nW > 1
    ? ' <span style="color:var(--warning);font-size:.75rem">(÷' + nW + ' ganadores)</span>'
    : '';
  prizeEl.innerHTML =
    '🎉 Premio BINGO: <strong style="color:var(--accent)">S/. ' + pool.toFixed(2) + '</strong>' + splitNote + '<br>' +
    '⭐ Premio LÍNEA: <strong style="color:var(--warning)">S/. ' + linea.toFixed(2) + '</strong>';
}

// ── v6: Mostrar ganadores en vivo ─────────────────────
function updateWinnersDisplay(winners, lineaWinners) {
  if ((!winners || !winners.length) && (!lineaWinners || !lineaWinners.length)) return;
  let wEl = document.getElementById('live-winners-display');
  if (!wEl) {
    wEl = document.createElement('div');
    wEl.id = 'live-winners-display';
    wEl.style.cssText = 'margin-top:10px;font-size:.82rem;border-top:1px solid var(--border);padding-top:8px';
    const statusEl = document.getElementById('game-status-msg');
    if (statusEl && statusEl.parentNode) {
      statusEl.parentNode.appendChild(wEl);
    }
  }
  let html = '';
  if (winners && winners.length) {
    html += '🏆 <strong>BINGO:</strong> ' +
      winners.map(function(w) {
        const p = Number(w.prize || 0).toFixed(2);
        return '<span style="color:var(--accent)">' + (w.nombre || w.id) + '</span> (S/. ' + p + ')';
      }).join(', ');
  }
  if (lineaWinners && lineaWinners.length) {
    if (html) html += '<br>';
    html += '⭐ <strong>LÍNEA:</strong> ' +
      lineaWinners.map(function(w) {
        const p = Number(w.linea_prize || 0).toFixed(2);
        return '<span style="color:var(--warning)">' + (w.nombre || w.id) + '</span> (S/. ' + p + ')';
      }).join(', ');
  }
  wEl.innerHTML = html;
}

// ── RELOJ ─────────────────────────────────────────────
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

// ── GAME OVER ─────────────────────────────────────────
function showGameOver() {
  const timerEl = document.getElementById('timer');
  const timer   = timerEl ? timerEl.textContent.replace('⏱ ', '') : '—';
  const infoEl  = document.getElementById('gameover-info');
  if (infoEl) infoEl.textContent = '¡Se sortearon las ' + gameBalls + ' bolillas en ' + timer + '!';
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
    c.style.width             = (Math.random() * 8 + 4) + 'px';
    c.style.height            = (Math.random() * 8 + 4) + 'px';
    document.body.appendChild(c);
    setTimeout(function() { c.remove(); }, 5000);
  }
}

// ── CARTILLA UI (multi-cartilla v8) ─────────────────────

function getCartillaState(cid) {
  if (!cartillaStates[cid]) {
    cartillaStates[cid] = { bingoFired: false, almostSpoken: false, claimedBingo: false, claimedLinea: false };
  }
  return cartillaStates[cid];
}

function playWinAlert() {
  if (!soundEnabled) return;
  var voice = (document.getElementById('player-voice-select') || {}).value || 'es-PE-CamilaNeural';
  playPhrase('¡Bingo! ¡Felicidades, ganaste!', voice);
}

function showWinNotification(title, body) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try { new Notification(title, { body: body }); } catch(e) {}
}

function getMyCartillasFromStorage() {
  try { return JSON.parse(localStorage.getItem('my_cartillas') || '[]') || []; }
  catch(e) { return []; }
}

function removeClaimButtons() {
  cartillaStates = {};
}

async function loadAllCartillas() {
  const ids = getMyCartillasFromStorage();
  if (!ids.length) { showToast('⚠️ No tienes cartillas guardadas en este dispositivo'); return; }

  // Ensure activeSessionId is known before filtering (avoid race on page load)
  if (activeSessionId === null) {
    try {
      const st = await fetch('/api/state');
      const sd = await st.json();
      if (sd.session_id) activeSessionId = sd.session_id;
    } catch(e) {}
  }

  showToast('⏳ Cargando ' + ids.length + ' cartilla(s)…');
  const results = await Promise.allSettled(
    ids.map(function(entry) {
      const cid = (typeof entry === 'object' && entry !== null) ? entry.id : entry;
      return fetch('/api/cartilla/' + String(cid).trim().toUpperCase())
        .then(function(r) { return r.ok ? r.json() : null; });
    })
  );
  const all = results
    .filter(function(r) { return r.status === 'fulfilled' && r.value && r.value.grid; })
    .map(function(r) { return r.value; });

  // Filtrar por sesión activa — si hay sesión activa, sólo cartillas de esa sesión
  let wrongSession = 0;
  if (activeSessionId) {
    myCartillas = all.filter(function(c) {
      if (c.session_id && c.session_id !== activeSessionId) {
        wrongSession++;
        return false;
      }
      return true;
    });
  } else {
    myCartillas = all;
  }

  cartillaStates = {};

  if (wrongSession > 0 && !myCartillas.length) {
    showToast('❌ Tus cartillas son de otra sesión y no pueden usarse en el juego actual.', 5000);
    return;
  }
  if (wrongSession > 0) {
    showToast('⚠️ ' + wrongSession + ' cartilla(s) de otra sesión fueron ignoradas.', 4000);
  }
  if (!myCartillas.length) { showToast("❌ No se encontraron cartillas válidas"); return; }

  const banner = document.getElementById('banner-comprar');
  if (banner) { banner.style.transition='opacity .4s'; banner.style.opacity='0'; setTimeout(function(){ banner.style.display='none'; }, 420); }
  updateMyCartillaAutoMark(true);
  showToast('✅ ' + myCartillas.length + ' cartilla(s) cargada(s)');
  showPlayerSessionBanner();
  var wrapEl = document.getElementById('mis-cartillas-wrap');
  if (wrapEl) { setTimeout(function(){ wrapEl.scrollIntoView({behavior:'smooth', block:'nearest'}); }, 700); }
  try { if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission(); } catch(e) {}
}

// ── Actualizar cartillas del jugador (todas) ──────────
function updateMyCartillaAutoMark(force) {
  const wrap   = document.getElementById('mis-cartillas-wrap');
  const status = document.getElementById('my-cartilla-status');
  if (!myCartillas.length) {
    if (status) status.textContent = gameStarted
      ? '💡 El juego ya empezó. Carga tus cartillas para marcar automáticamente.'
      : 'Haz clic en "Cargar mis cartillas" para empezar.';
    if (wrap) wrap.innerHTML = '';
    return;
  }
  if (status) status.textContent = '';
  const drawnSet = new Set(drawnLocal);
  const voice = (document.getElementById('player-voice-select') || {}).value || 'es-PE-CamilaNeural';
  myCartillas.forEach(function(cart) {
    const state   = getCartillaState(cart.id);
    const panelId = 'mc-panel-' + cart.id;
    let panel = document.getElementById(panelId);
    if (!panel) {
      panel = document.createElement('div');
      panel.id        = panelId;
      panel.className = 'mc-panel';
      if (wrap) wrap.appendChild(panel);
    }
    const nums = [];
    for (const row of cart.grid) for (const n of row) if (n !== null && n !== undefined) nums.push(n);
    const marked  = nums.filter(function(n) { return drawnSet.has(n); }).length;
    function cellOk(r, c) { return cart.grid[r][c] === null || drawnSet.has(cart.grid[r][c]); }
    var isLinea = false, lineaType = null, lineaIdx = null;
    const isBingo = [0,1,2,3,4].every(function(r){ return [0,1,2,3,4].every(function(c){ return cellOk(r,c); }); });
    for (var r=0;r<5&&!isLinea;r++) if ([0,1,2,3,4].every(function(c){return cellOk(r,c);})) { isLinea=true; lineaType='row'; lineaIdx=r; }
    for (var c=0;c<5&&!isLinea;c++) if ([0,1,2,3,4].every(function(r){return cellOk(r,c);})) { isLinea=true; lineaType='col'; lineaIdx=c; }
    if (!isLinea && [0,1,2,3,4].every(function(i){return cellOk(i,i);}))   { isLinea=true; lineaType='diag_main'; }
    if (!isLinea && [0,1,2,3,4].every(function(i){return cellOk(i,4-i);})) { isLinea=true; lineaType='diag_anti'; }
    const isAlmost = !isBingo && (nums.length - marked === 1);
    if (isBingo && !state.bingoFired) {
      state.bingoFired = true;
      playWinAlert();
      showWinNotification('🎉 BINGO!', 'Cartilla ' + cart.id);
    }
    // Auto-claim bingo (only once, non-blocking)
    if (isBingo && !state.claimedBingo && !state.claimingBingo) {
      state.claimingBingo = true;
      claimBingo(cart.id);
    }
    // Auto-claim línea (only once, non-blocking)
    if (isLinea && !isBingo && !state.claimedLinea && !state.claimingLinea) {
      state.claimingLinea = true;
      claimLinea(cart.id);
    }
    if (isAlmost && !state.almostSpoken) {
      state.almostSpoken = true;
      if (soundEnabled) playPhrase('¡Falta uno!', voice);
    }
    const badge = isBingo  ? '<span class="mc-badge mc-badge-bingo">🎉 BINGO</span>'
                : isLinea  ? '<span class="mc-badge mc-badge-linea">⭐ LÍNEA</span>'
                : isAlmost ? '<span class="mc-badge mc-badge-almost">🔥 FALTA 1</span>'
                : '';
    let header = panel.querySelector('.mc-header');
    if (!header) {
      header = document.createElement('div');
      header.className = 'mc-header';
      panel.insertBefore(header, panel.firstChild);
    }
    header.innerHTML = '<div><span class="mc-id">Cartilla ' + cart.id + '</span>' +
      '<span class="mc-count"> · ' + marked + '/24</span></div>' + badge;

    // Rebuild grid if type changed (detect by data attribute)
    let gridWrap = panel.querySelector('.mc-grid-wrap');
    if (!gridWrap) {
      gridWrap = document.createElement('div');
      gridWrap.className = 'mc-grid-wrap';
      const colsDiv = document.createElement('div');
      colsDiv.className = 'mc-grid-cols';
      BINGO75_COLORS.forEach(function(col) {
        const d = document.createElement('div');
        d.className = 'mc-col-label';
        d.textContent = col.letter;
        d.style.color      = col.fg;
        d.style.background = col.bg;
        colsDiv.appendChild(d);
      });
      gridWrap.appendChild(colsDiv);
      const gridDiv = document.createElement('div');
      gridDiv.className = 'mc-grid';
      gridDiv.style.gridTemplateColumns = 'repeat(5,1fr)';
      for (let i = 0; i < 25; i++) {
        const cell = document.createElement('div');
        cell.className = 'mc-cell';
        gridDiv.appendChild(cell);
      }
      gridWrap.appendChild(gridDiv);
      panel.appendChild(gridWrap);
    }

    const cells = panel.querySelectorAll('.mc-cell');
    let idx = 0;
    for (let ri = 0; ri < 5; ri++) {
      for (let ci = 0; ci < 5; ci++) {
        const num   = cart.grid[ri][ci];
        const g     = BINGO75_COLORS[ci];
        const cell  = cells[idx++];
        if (!cell) continue;
        cell.className = 'mc-cell';
        cell.innerHTML = '';
        cell.style.borderColor = '';
        cell.style.opacity = '';

        // Highlight winning line
        var inLine = false;
        if (isLinea) {
          if (lineaType === 'row'       && ri === lineaIdx)  inLine = true;
          if (lineaType === 'col'       && ci === lineaIdx)  inLine = true;
          if (lineaType === 'diag_main' && ri === ci)        inLine = true;
          if (lineaType === 'diag_anti' && ri + ci === 4)    inLine = true;
        }

        const isFree = isBingo75 && ri === 2 && ci === 2;
        if (isFree) {
          cell.classList.add('marked');
          cell.style.borderColor = g.fg;
          cell.style.background  = g.bg;
          cell.innerHTML = '<span style="background:' + g.fg + ';font-size:.6rem">FREE</span>';
        } else if (num === null || num === undefined) {
          cell.classList.add('empty');
          cell.textContent = '·';
        } else if (drawnSet.has(num)) {
          cell.classList.add('marked');
          cell.style.borderColor = g.fg;
          const s = document.createElement('span');
          s.textContent = num; s.style.background = g.fg;
          cell.appendChild(s);
        } else {
          cell.textContent = num;
          cell.style.color = g.fg + '44';
        }
        if (inLine) cell.style.boxShadow = '0 0 0 2px ' + g.fg + ', inset 0 0 8px ' + g.fg + '44';
      }
    }
    // Show auto-claim status (no manual buttons)
    var statusDiv = panel.querySelector('.mc-claim-status');
    if (!statusDiv) {
      statusDiv = document.createElement('div');
      statusDiv.className = 'mc-claim-status';
      statusDiv.style.cssText = 'padding:6px 10px;font-size:.75rem;font-weight:700;text-align:center';
      panel.appendChild(statusDiv);
    }
    if (isBingo && state.claimedBingo) {
      statusDiv.innerHTML = '<span style="color:var(--accent)">✅ Bingo enviado — el admin verificará tu cartilla</span>';
    } else if (isBingo && state.claimingBingo) {
      statusDiv.innerHTML = '<span style="color:var(--muted)">⏳ Enviando bingo…</span>';
    } else if (isLinea && state.claimedLinea) {
      statusDiv.innerHTML = '<span style="color:#f6c343">⭐ Línea registrada — sigue jugando</span>';
    } else if (isLinea && state.claimingLinea) {
      statusDiv.innerHTML = '<span style="color:var(--muted)">⏳ Registrando línea…</span>';
    } else {
      statusDiv.innerHTML = '';
    }
  });
}

// ── Banner de sesión del jugador ─────────────────────
async function showPlayerSessionBanner() {
  const banner = document.getElementById('player-session-banner');
  if (!banner) return;

  const stored = getMyCartillasFromStorage();
  if (!stored.length) { banner.style.display = 'none'; return; }

  // Collect unique session IDs from stored cartillas
  const sids = [...new Set(
    stored.map(e => (typeof e === 'object' && e !== null) ? e.session_id : null).filter(Boolean)
  )];
  if (!sids.length) { banner.style.display = 'none'; return; }

  // Fetch session info for all unique sids
  const sessions = [];
  for (const sid of sids) {
    try {
      const res = await fetch('/api/session/' + encodeURIComponent(sid));
      if (res.ok) { const d = await res.json(); if (d.session) sessions.push(d.session); }
    } catch(e) {}
  }
  if (!sessions.length) { banner.style.display = 'none'; return; }

  const statusMap = {
    scheduled: { emoji:'⏳', label:'Programada',        bg:'rgba(100,130,160,.12)', border:'rgba(100,130,160,.3)', color:'var(--muted)' },
    preparing: { emoji:'🔄', label:'Iniciando pronto',  bg:'rgba(246,195,67,.12)',  border:'rgba(246,195,67,.4)',  color:'#f6c343' },
    active:    { emoji:'🟢', label:'EN JUEGO AHORA',    bg:'rgba(0,229,180,.12)',   border:'rgba(0,229,180,.5)',   color:'var(--accent)' },
    finished:  { emoji:'✅', label:'Finalizada',         bg:'rgba(100,130,160,.08)', border:'rgba(100,130,160,.2)', color:'var(--muted)' },
  };

  const esc2 = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const html = sessions.map(s => {
    const st = statusMap[s.status] || statusMap.scheduled;
    const isActive   = s.status === 'active';
    const isPreparing = s.status === 'preparing';
    const activeNote  = isActive    ? '<span style="font-size:.72rem;color:var(--accent);margin-left:8px">¡Entra al juego!</span>' : '';
    const prepNote    = isPreparing ? '<span style="font-size:.72rem;color:#f6c343;margin-left:8px">A punto de iniciar</span>' : '';
    const sessionNote = s.id === activeSessionId ? '' :
      (activeSessionId
        ? '<div style="font-size:.72rem;color:var(--muted);margin-top:3px">Esta sesión no es la que está activa ahora mismo.</div>'
        : '<div style="font-size:.72rem;color:var(--muted);margin-top:3px">Aún no hay sesión activa. Tu cartilla estará lista cuando el admin inicie.</div>');
    return `<div style="padding:10px 14px;background:${st.bg};border:1px solid ${st.border};border-radius:10px;margin-bottom:6px">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-size:.85rem;font-weight:700">${st.emoji} ${esc2(s.bingo_nombre||'Bingo')}</span>
        <span style="font-size:.75rem;color:${st.color};font-weight:700">${st.label}</span>
        ${activeNote}${prepNote}
      </div>
      <div style="font-size:.72rem;color:var(--muted);margin-top:2px">📅 ${esc2(s.date||'—')} &nbsp;🕐 ${esc2(s.time||'—')}</div>
      ${sessionNote}
    </div>`;
  }).join('');

  banner.innerHTML = html;
  banner.style.display = 'block';
}

function initMyCartillaUI() {
  const btn = document.getElementById('btn-load-cartilla');
  if (btn) btn.addEventListener('click', loadAllCartillas);
  showPlayerSessionBanner();
  if (getMyCartillasFromStorage().length) setTimeout(loadAllCartillas, 800);
}

// ── Auto-reclamar BINGO ───────────────────────────────
async function claimBingo(cid) {
  const state = getCartillaState(cid);
  try {
    const res  = await fetch('/api/winner/claim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cid: cid }),
    });
    const data = await res.json();
    // Mark claimed even on "already" so we don't retry
    if (data.already || (res.ok && data.ok)) {
      state.claimedBingo = true;
    } else {
      state.claimingBingo = false; // allow retry next cycle if server error
    }
    if (!res.ok || !data.ok) return;
    const winner = data.winner || {};
    const prize  = Number(data.prize_each || winner.prize || 0).toFixed(2);
    const nW     = data.n_winners || 1;
    showWinNotification('🎉 ¡BINGO!', 'Premio: S/. ' + prize);
    // The global winner banner will appear for all players via syncState
  } catch(e) {
    state.claimingBingo = false; // retry on next cycle
  }
}

// ── Auto-reclamar LÍNEA ───────────────────────────────
async function claimLinea(cid) {
  const state = getCartillaState(cid);
  try {
    const res  = await fetch('/api/winner/claim_linea', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cid: cid }),
    });
    const data = await res.json();
    if (data.already || data.error === 'linea_closed' || (res.ok && data.ok)) {
      state.claimedLinea = true;
    } else {
      state.claimingLinea = false;
    }
    // The global línea banner will appear for all players via syncState
  } catch(e) {
    state.claimingLinea = false;
  }
}

// ── Pantalla de victoria del jugador ─────────────────
function showGameOverPlayer(winner) {
  const go   = document.getElementById('gameover');
  if (!go) return;
  const info = document.getElementById('gameover-info');
  if (info && winner) {
    const prize  = Number(winner.prize || 0).toFixed(2);
    const nW     = winner.n_winners || 1;
    const splitLine = (winner.split && nW > 1)
      ? '<br><span style="color:var(--warning);font-size:.85rem">Empate entre ' + nW + ' ganadores — S/. ' + prize + ' cada uno</span>'
      : '';
    const yapePlin = (localStorage.getItem('bingo_yape_plin') || '').trim();
    const yapeBox  = yapePlin
      ? '<div style="margin-top:14px;padding:10px 16px;background:rgba(0,229,180,.08);border:1px solid rgba(0,229,180,.3);border-radius:10px;font-size:.85rem">' +
        '<div style="color:var(--muted);margin-bottom:4px">Tu premio sera enviado a Yape/Plin:</div>' +
        '<div style="font-size:1.2rem;font-weight:900;color:var(--accent);letter-spacing:2px">' + yapePlin + '</div>' +
        '<div style="font-size:.72rem;color:var(--muted);margin-top:4px">Numero que registraste al comprar tu cartilla</div>' +
        '</div>'
      : '<div style="margin-top:10px;font-size:.8rem;color:var(--muted)">El premio sera enviado al Yape/Plin que registraste.</div>';
    info.innerHTML = '¡Felicidades <strong>' + (winner.nombre || '') + '</strong>!<br>' +
      'Premio: <span style="color:var(--accent);font-size:1.3rem">S/. ' + prize + '</span> 🎉' + splitLine + yapeBox;
  }
  go.classList.add('show');
  launchConfetti();
  if (soundEnabled) {
    playPhrase('¡Felicidades! ¡Ganaste el bingo!',
      (document.getElementById('player-voice-select') || {}).value || 'es-PE-CamilaNeural');
  }
}

// ── PAUSA POR GANADOR (overlay jugador) ───────────────
let pausedOverlayShown = false;

function showPausedOverlay(winners) {
  if (pausedOverlayShown) return;
  pausedOverlayShown = true;

  const myIds = myCartillas.map(function(c) { return c.id; });
  const iWon  = myIds.length > 0 && winners.some(function(w) { return myIds.includes(w.id); });

  let existing = document.getElementById('player-paused-overlay');
  if (!existing) {
    existing = document.createElement('div');
    existing.id = 'player-paused-overlay';
    existing.style.cssText = [
      'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);',
      'background:var(--panel);border:2px solid var(--accent);',
      'border-radius:16px;padding:18px 28px;z-index:400;',
      'text-align:center;max-width:380px;width:90%;',
      'box-shadow:0 0 30px rgba(0,229,180,.2);',
    ].join('');
    document.body.appendChild(existing);
  }

  if (iWon) {
    const myWinner = winners.find(function(w) { return myIds.includes(w.id); });
    const prize    = myWinner ? Number(myWinner.prize || 0).toFixed(2) : '0.00';
    const nW       = myWinner ? (myWinner.n_winners || 1) : 1;
    const splitMsg = (myWinner && myWinner.split && nW > 1)
      ? '<div style="font-size:.8rem;color:var(--warning);margin-top:4px">Empate entre ' + nW + ' jugadores · S/. ' + prize + ' cada uno</div>'
      : '<div style="font-size:.9rem;color:var(--accent);margin-top:4px;font-weight:700">Premio: S/. ' + prize + '</div>';
    const yapePlin = (localStorage.getItem('bingo_yape_plin') || '').trim();
    const yapeBox  = yapePlin
      ? '<div style="margin-top:10px;padding:8px 12px;background:rgba(0,229,180,.08);border:1px solid rgba(0,229,180,.3);border-radius:8px">' +
        '<div style="font-size:.7rem;color:var(--muted)">Tu premio sera enviado a Yape/Plin:</div>' +
        '<div style="font-size:1.1rem;font-weight:900;color:var(--accent);letter-spacing:2px">' + yapePlin + '</div>' +
        '</div>'
      : '';
    existing.style.borderColor = 'var(--accent)';
    existing.innerHTML = [
      '<div style="font-size:2.5rem;margin-bottom:4px;">🎉</div>',
      '<div style="font-family:Bebas Neue,sans-serif;font-size:2rem;color:var(--accent);letter-spacing:3px;">¡GANASTE!</div>',
      splitMsg,
      yapeBox,
      '<div style="font-size:.8rem;color:var(--muted);margin-top:6px;">El admin verificará tu cartilla.</div>',
    ].join('');
    playWinAlert();
  } else {
    const names = winners.map(function(w) {
      return '<strong style="color:var(--accent)">' + (w.nombre || w.id) + '</strong>';
    }).join(', ');
    existing.style.borderColor = 'var(--warning)';
    existing.innerHTML = [
      '<div style="font-size:1.5rem;margin-bottom:4px;">⏸</div>',
      '<div style="font-weight:700;color:var(--warning);margin-bottom:6px;">Juego Pausado</div>',
      '<div style="font-size:.85rem;color:var(--muted);">',
        names ? ('Ganador(es): ' + names) : 'Hay un ganador.',
      '</div>',
      '<div style="font-size:.75rem;color:var(--muted);margin-top:5px;">Esperando al administrador…</div>',
    ].join('');
  }
}

function hidePausedOverlay() {
  if (!pausedOverlayShown) return;
  pausedOverlayShown = false;
  var el = document.getElementById('player-paused-overlay');
  if (el) el.remove();
}

// ── Barra de estadísticas ──────────────────────────────
async function updateGameStats() {
  try {
    const res  = await fetch('/api/game_stats');
    if (!res.ok) return;
    const d = await res.json();

    const bar = document.getElementById('game-stats-bar');
    if (!bar) return;

    // Solo mostrar si hay cartillas en juego
    if (d.n_cartillas > 0) {
      bar.style.display = 'block';
    }

    // Línea 1
    const pl = document.getElementById('gst-players');
    const ca = document.getElementById('gst-cartillas');
    if (pl) pl.textContent = d.n_players + (d.n_players === 1 ? ' jugador' : ' jugadores');
    if (ca) ca.textContent = d.n_cartillas + (d.n_cartillas === 1 ? ' cartilla' : ' cartillas');

    // Línea 2: cerca de ganar
    const cl = document.getElementById('gst-close');
    if (cl) {
      const close = d.close_to_win || {};
      const keys  = Object.keys(close).map(Number).sort((a,b) => a-b);
      if (keys.length === 0) {
        cl.textContent = 'Ninguno muy cerca aún';
        cl.style.color = 'var(--muted)';
      } else {
        cl.innerHTML = keys.map(k => {
          const n = close[String(k)];
          const color = k === 1 ? '#ff4d4d' : k === 2 ? '#ff8c42' : '#f6c343';
          return `<span style="color:${color};margin-right:10px"><strong>${n}</strong> ${n===1?'cartilla':'cartillas'} a <strong>${k}</strong> ${k===1?'bolilla':'bolillas'}</span>`;
        }).join('');
      }
    }

    // Línea 3: premios
    const pb = document.getElementById('gst-prize-bingo');
    const pl2 = document.getElementById('gst-prize-linea');
    if (pb) pb.textContent = d.prize_bingo > 0 ? 'S/. ' + d.prize_bingo.toFixed(2) : '—';
    if (pl2) pl2.textContent = d.prize_linea > 0 ? 'S/. ' + d.prize_linea.toFixed(2) : '—';
  } catch(e) {}
}

// ── CHAT ──────────────────────────────────────────────
let chatLastId   = 0;
let chatSending  = false;

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function fetchChatMessages() {
  try {
    const res  = await fetch('/api/chat/messages?since=' + chatLastId);
    if (!res.ok) return;
    const data = await res.json();
    const msgs = data.messages || [];
    if (!msgs.length) return;

    const box   = document.getElementById('chat-messages');
    const empty = document.getElementById('chat-empty');
    if (!box) return;
    if (empty) empty.remove();

    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    const myName   = (localStorage.getItem('chat_name') || '').trim();

    msgs.forEach(function(m) {
      const isMe = myName && m.name === myName;
      const div  = document.createElement('div');
      div.style.cssText = 'display:flex;flex-direction:column;align-items:' + (isMe ? 'flex-end' : 'flex-start');
      div.innerHTML =
        '<div style="max-width:85%;background:' + (isMe ? 'rgba(0,229,180,.15)' : 'rgba(255,255,255,.05)') + ';' +
        'border:1px solid ' + (isMe ? 'rgba(0,229,180,.3)' : 'rgba(255,255,255,.08)') + ';' +
        'border-radius:10px;padding:5px 10px;font-size:.8rem;">' +
        '<span style="font-weight:700;color:' + esc(m.color) + ';font-size:.72rem">' + esc(m.name) + '</span>' +
        '<span style="color:rgba(255,255,255,.3);font-size:.65rem;margin-left:6px">' + esc(m.ts) + '</span>' +
        '<div style="color:#e8f4f8;margin-top:2px;word-break:break-word">' + esc(m.msg) + '</div>' +
        '</div>';
      box.appendChild(div);
      chatLastId = Math.max(chatLastId, m.id);
    });

    if (atBottom) box.scrollTop = box.scrollHeight;

    // Actualizar contador
    const onlineEl = document.getElementById('chat-online');
    if (onlineEl) onlineEl.textContent = chatLastId + (chatLastId === 1 ? ' mensaje' : ' mensajes');
  } catch(e) {}
}

async function sendChatMsg() {
  if (chatSending) return;
  const nameEl = document.getElementById('chat-name');
  const msgEl  = document.getElementById('chat-msg');
  if (!nameEl || !msgEl) return;

  const name = nameEl.value.trim();
  const msg  = msgEl.value.trim();
  if (!name) { nameEl.focus(); showToast('⚠️ Ingresa tu nombre'); return; }
  if (!msg)  { msgEl.focus();  return; }

  chatSending = true;
  try {
    const res  = await fetch('/api/chat/send', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ name, msg }),
    });
    const data = await res.json();
    if (!res.ok) { showToast('❌ ' + (data.error || 'Error')); return; }
    msgEl.value = '';
    localStorage.setItem('chat_name', name);
    await fetchChatMessages();
    const box = document.getElementById('chat-messages');
    if (box) box.scrollTop = box.scrollHeight;
  } catch(e) {
    showToast('❌ Error de conexión');
  } finally {
    chatSending = false;
  }
}

function initChat() {
  // Restaurar nombre guardado
  const saved = localStorage.getItem('chat_name');
  const nameEl = document.getElementById('chat-name');
  if (saved && nameEl) nameEl.value = saved;

  fetchChatMessages();
  setInterval(fetchChatMessages, 3000);
}

// ── INIT ──────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  const urlEl = document.getElementById('server-url');
  if (urlEl) urlEl.textContent = window.location.href;

  initGrid();
  initMyCartillaUI();
  initDrum();

  syncState();
  setInterval(syncState, 1500);

  updateGameStats();
  setInterval(updateGameStats, 10000);

  initChat();
});