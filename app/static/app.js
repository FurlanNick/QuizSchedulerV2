/* ══════════════════════════════════════════════════════════════════════════
   QuizMeet Scheduler — frontend application
   ══════════════════════════════════════════════════════════════════════════ */

// ── State ──────────────────────────────────────────────────────────────────
let SESSION_ID = null;
let STATE = null;   // mirrors ProgramState from server

// ── Helpers ────────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    let msg = 'Server error';
    if (err.detail) {
      if (Array.isArray(err.detail)) {
        msg = err.detail.map(d => d.msg || JSON.stringify(d)).join(', ');
      } else if (typeof err.detail === 'object') {
        msg = JSON.stringify(err.detail);
      } else {
        msg = err.detail;
      }
    }
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type}`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), 3500);
}

function spinner(show, msg = 'Generating schedule…') {
  document.getElementById('spinner-overlay').classList.toggle('hidden', !show);
  document.getElementById('spinner-msg').textContent = msg;
}

function setView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

function switchTab(name) {
  document.querySelectorAll('.nav-item').forEach(el =>
    el.classList.toggle('active', el.dataset.tab === name));
  document.querySelectorAll('.tab-section').forEach(el =>
    el.classList.toggle('active', el.id === `tab-${name}`));
  if (name === 'schedule') renderMeets();
  if (name === 'cross')    renderCrossRefDropdown();
}

// ── Data Management ────────────────────────────────────────────────────────
function exportSeason() {
  if (!STATE) return;
  const data = JSON.stringify(STATE, null, 2);
  const blob = new Blob([data], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `quiz_season_${STATE.owner_name.replace(/\s+/g, '_')}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

async function importSeason(event) {
  const file = event.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = async (e) => {
    try {
      const importedState = JSON.parse(e.target.result);
      if (!importedState.owner_name || !importedState.all_teams) {
        throw new Error('Invalid season data file.');
      }

      const res = await api('POST', '/api/import', {
        session_id: SESSION_ID,
        state: importedState
      });

      await refreshState();
      populateUIFromState();
      toast('Season data imported successfully!', 'success');
    } catch (err) {
      toast('Import failed: ' + err.message, 'error');
    } finally {
      event.target.value = ''; // Reset file input
    }
  };
  reader.readAsText(file);
}

// ── Sign-in ────────────────────────────────────────────────────────────────
async function doSignIn() {
  const name = document.getElementById('signin-name').value.trim();
  if (!name) { toast('Please enter a name.', 'error'); return; }
  resetUI();
  try {
    const res = await api('POST', '/api/signin', { name });
    SESSION_ID = res.session_id;
    await refreshState();
    document.getElementById('nav-user').textContent = res.name;
    setView('view-app');
    toast(res.is_new ? `Welcome, ${res.name}!` : `Welcome back, ${res.name}!`, 'success');
    populateUIFromState();
  } catch (e) { toast(e.message, 'error'); }
}

function doSignOut() {
  SESSION_ID = null; STATE = null;
  resetUI();
  setView('view-signin');
  document.getElementById('signin-name').value = '';
}

function resetUI() {
  // Setup fields
  document.getElementById('cfg-meets').value = 4;
  document.getElementById('cfg-rooms').value = 5;
  document.getElementById('cfg-slots').value = 6;
  document.getElementById('cfg-mpt').value   = 3;
  document.getElementById('cfg-type').value  = 'district';

  // Roster
  document.getElementById('roster-textarea').value = '';
  document.getElementById('team-pool-container').innerHTML = '';
  const btnRemoveAll = document.getElementById('btn-remove-all');
  if (btnRemoveAll) btnRemoveAll.classList.add('hidden');

  // Schedule
  document.getElementById('meets-container').innerHTML = '';
  document.getElementById('gen-status').textContent = '';

  // Cross-ref
  document.getElementById('cross-table-wrap').innerHTML = '';
  document.getElementById('cross-up-to').innerHTML = '';
}

document.getElementById('signin-name').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSignIn();
});

// ── State refresh ──────────────────────────────────────────────────────────
async function refreshState() {
  STATE = await api('GET', `/api/state/${SESSION_ID}`);
}

function populateUIFromState() {
  if (!STATE) return;

  // Setup fields
  if (STATE.config) {
    document.getElementById('cfg-meets').value  = STATE.config.n_quiz_meets;
    document.getElementById('cfg-rooms').value  = STATE.config.n_rooms;
    document.getElementById('cfg-slots').value  = STATE.config.n_time_slots;
    document.getElementById('cfg-mpt').value    = STATE.config.matches_per_team;
    document.getElementById('cfg-type').value   = STATE.config.tournament_type;
  }

  // Roster
  renderTeamPool();

  renderMeets();
}

// ── Setup ──────────────────────────────────────────────────────────────────
async function saveSetup() {
  const newConfig = {
    n_quiz_meets:     +document.getElementById('cfg-meets').value,
    n_rooms:          +document.getElementById('cfg-rooms').value,
    n_time_slots:     +document.getElementById('cfg-slots').value,
    n_teams:          STATE?.all_teams?.length || 10,
    matches_per_team: +document.getElementById('cfg-mpt').value,
    tournament_type:  document.getElementById('cfg-type').value,
  };

  // Setup is now always destructive. Warn if there is existing data.
  const hasData = (STATE?.meets?.length > 0) || (STATE?.all_teams?.length > 0);
  if (hasData) {
    if (!confirm("Warning: Saving a new setup will erase all currently generated teams and schedules. This allows for a fresh start. Continue?")) {
      return;
    }
  }

  try {
    await api('POST', '/api/setup', { session_id: SESSION_ID, config: newConfig });
    await refreshState();
    populateUIFromState();
    document.getElementById('setup-status').textContent = '✓ Saved';
    setTimeout(() => document.getElementById('setup-status').textContent = '', 2500);
  } catch (e) { toast(e.message, 'error'); }
}

// ── Roster ─────────────────────────────────────────────────────────────────
function renderTeamPool() {
  const container = document.getElementById('team-pool-container');
  const btnRemoveAll = document.getElementById('btn-remove-all');

  if (!STATE?.all_teams?.length) {
    container.innerHTML = '<p style="color:var(--ink-3);font-size:.82rem">No teams in pool.</p>';
    if (btnRemoveAll) btnRemoveAll.classList.add('hidden');
    return;
  }

  if (btnRemoveAll) btnRemoveAll.classList.remove('hidden');
  container.innerHTML = STATE.all_teams.map((t, i) => `
    <div class="team-pill">
      <span>${esc(t)}</span>
      <button class="btn-danger" onclick="deleteTeam(${i})">✕</button>
    </div>
  `).join('');
}

async function saveRoster() {
  const raw = document.getElementById('roster-textarea').value;
  const newTeams = raw.split('\n').map(s => s.trim()).filter(Boolean);
  
  // Merge with existing teams, avoiding duplicates
  const existingTeams = STATE?.all_teams || [];
  const updatedTeams = [...existingTeams];
  
  newTeams.forEach(nt => {
    if (!updatedTeams.includes(nt)) {
      updatedTeams.push(nt);
    }
  });

  if (updatedTeams.length < 3) { toast('Need at least 3 teams in total.', 'error'); return; }
  
  try {
    await api('POST', '/api/roster', { session_id: SESSION_ID, teams: updatedTeams });
    await refreshState();
    document.getElementById('roster-textarea').value = '';
    renderTeamPool();
    document.getElementById('roster-status').textContent = `✓ Pool updated`;
    setTimeout(() => document.getElementById('roster-status').textContent = '', 2500);
    renderMeets();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteTeam(idx) {
  const updatedTeams = [...STATE.all_teams];
  const removed = updatedTeams.splice(idx, 1)[0];
  if (updatedTeams.length > 0 && updatedTeams.length < 3) {
    toast('Cannot have fewer than 3 teams (unless clearing all).', 'error');
    return;
  }

  let msg = `Are you sure you want to remove "${removed}" from the pool?`;
  const hasUnlocked = STATE?.meets?.some(m => !m.is_locked);
  if (hasUnlocked) {
    msg += "\n\nWarning: Removing a team will reset any unlocked schedules.";
  }
  if (!confirm(msg)) return;

  try {
    await api('POST', '/api/roster', { session_id: SESSION_ID, teams: updatedTeams });
    await refreshState();
    renderTeamPool();
    renderMeets();
    toast(`Removed ${removed}`, 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function clearRoster() {
  let msg = "Are you sure you want to remove ALL teams from the pool?";
  const hasUnlocked = STATE?.meets?.some(m => !m.is_locked);
  if (hasUnlocked) {
    msg += "\n\nWarning: This will reset any unlocked schedules.";
  }
  if (!confirm(msg)) return;

  try {
    await api('POST', '/api/roster', { session_id: SESSION_ID, teams: [] });
    await refreshState();
    renderTeamPool();
    renderMeets();
    toast('Team pool cleared', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

// ── Team changes ───────────────────────────────────────────────────────────

// ── Schedule tab ───────────────────────────────────────────────────────────
function renderMeets() {
  const cfg = STATE?.config;
  const container = document.getElementById('meets-container');
  if (!cfg) {
    container.innerHTML = '<p style="color:var(--ink-2)">Complete Setup first.</p>';
    return;
  }

  const meetMap = {};
  (STATE.meets || []).forEach(m => { meetMap[m.meet_number] = m; });

  container.innerHTML = '';
  for (let mn = 1; mn <= cfg.n_quiz_meets; mn++) {
    const m = meetMap[mn];
    container.appendChild(buildMeetCard(mn, m, cfg));
  }
}

function buildMeetCard(meetNum, meet, cfg) {
  const el = document.createElement('div');
  el.className = `meet-card${meet?.is_locked ? ' locked' : ''}`;
  el.id = `meet-card-${meetNum}`;

  const status = !meet ? 'pending' : meet.is_locked ? 'locked' : 'ready';
  const statusLabel = status === 'pending' ? '⬡ Not generated'
                    : status === 'locked'  ? '✓ Locked'
                    :                        '● Ready';
  let nActive = meet?.active_team_ids?.length;
  if (nActive === undefined) {
    // Calculate expected active teams based on current pool + changes
    const active = new Set(STATE.all_teams.map((_, i) => i + 1));
    (STATE.team_changes || []).forEach(ch => {
      if (ch.effective_after_meet < meetNum) {
        const idx = STATE.all_teams.indexOf(ch.team_name);
        if (idx !== -1) {
          if (ch.action === 'remove') active.delete(idx + 1);
          else if (ch.action === 'add') {
             // In case team was added to all_teams later
             active.add(idx + 1);
          }
        }
      }
    });
    nActive = active.size;
  }
  const relaxed = meet?.constraints_relaxed?.length
    ? `<br><span style="color:var(--orange);font-size:.76rem">⚠ Relaxed: ${meet.constraints_relaxed.join(', ')}</span>` : '';

  el.innerHTML = `
    <div class="meet-card-header">
      <span class="meet-title">Quiz Meet ${meetNum}</span>
      <span class="meet-badge ${status}">${statusLabel}</span>
    </div>
    <div class="meet-card-body">
      <div class="meet-meta">
        ${meet ? `${nActive} active teams · ${meet.rooms.length} rooms scheduled${relaxed}` : `${nActive} teams expected`}
      </div>
      <div class="meet-actions">
        ${!meet?.is_locked
          ? `<button class="btn-primary" onclick="generateMeet(${meetNum})">
               ${meet ? '↺ Regenerate' : '▶ Generate'}
             </button>`
          : ''}
        ${meet && !meet.is_locked
          ? `<button class="btn-secondary" onclick="viewMeet(${meetNum})">View</button>
             <button class="btn-ghost" onclick="lockMeet(${meetNum})">🔒 Lock</button>`
          : ''}
        ${meet?.is_locked
          ? `<button class="btn-secondary" onclick="viewMeet(${meetNum})">View</button>` : ''}
      </div>
    </div>
  `;
  return el;
}

async function generateMeet(meetNum) {
  spinner(true, `Generating Meet ${meetNum}…`);
  try {
    await api('POST', '/api/generate', {
      session_id: SESSION_ID, meet_numbers: [meetNum]
    });
    await refreshState();
    renderMeets();
    document.getElementById('gen-status').textContent =
      `✓ Meet ${meetNum} generated successfully.`;
    toast(`Meet ${meetNum} generated!`, 'success');
  } catch (e) {
    toast(e.message, 'error');
    document.getElementById('gen-status').textContent = `✗ ${e.message}`;
  } finally {
    spinner(false);
  }
}

async function lockMeet(meetNum) {
  try {
    await api('POST', '/api/lock-meet', { session_id: SESSION_ID, meet_number: meetNum });
    await refreshState();
    renderMeets();
    toast(`Meet ${meetNum} locked.`, 'success');
  } catch (e) { toast(e.message, 'error'); }
}

// ── Meet detail modal ──────────────────────────────────────────────────────
function viewMeet(meetNum) {
  const meet = STATE.meets.find(m => m.meet_number === meetNum);
  if (!meet) return;

  const rooms = [...new Set(meet.rooms.map(r => r.room))].sort((a,b) => a - b);
  const slots = [...new Set(meet.rooms.map(r => r.time_slot))].sort((a,b) => a - b);

  const header = rooms.map(r => `<th>Room ${r}</th>`).join('');

  const rows = [];
  slots.forEach(slot => {
    ['A', 'B', 'C'].forEach(pos => {
      let rowHtml = '<tr>';
      rooms.forEach(roomNum => {
        const roomData = meet.rooms.find(r => r.room === roomNum && r.time_slot === slot);
        const posIdx = (pos === 'A' ? 0 : pos === 'B' ? 1 : 2);
        const tName = roomData ? roomData.team_names[posIdx] : null;
        const posClass = pos === 'A' ? 'pos-a' : pos === 'B' ? 'pos-b' : 'pos-c';

        rowHtml += `<td>
          ${tName ? `<div class="${posClass}">${esc(tName)}</div>` : '<div class="empty-cell">—</div>'}
        </td>`;
      });
      rowHtml += '</tr>';
      rows.push(rowHtml);
    });
  });

  const relaxNote = meet.constraints_relaxed.length
    ? `<p style="color:var(--orange);margin-bottom:12px;font-size:.82rem">⚠ Constraints relaxed: ${meet.constraints_relaxed.join(', ')}</p>` : '';

  const html = `
    <div class="modal-overlay" onclick="if(event.target===this)this.remove()">
      <div class="modal">
        <div class="modal-header">
          <h3>Quiz Meet ${meetNum} — Schedule</h3>
          <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">✕</button>
        </div>
        <div class="modal-body">
          ${relaxNote}
          <p style="color:var(--ink-2);font-size:.82rem;margin-bottom:10px">
            ${meet.active_team_ids.length} active teams · ${meet.rooms.length} rooms
            ${meet.is_locked ? '· <span style="color:var(--green)">🔒 Locked</span>' : ''}
          </p>
          <div class="sched-table-wrap">
            <table class="sched-grid transposed">
              <thead><tr>${header}</tr></thead>
              <tbody>${rows.join('')}</tbody>
            </table>
          </div>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}

// ── Cross-reference tab ────────────────────────────────────────────────────
function renderCrossRefDropdown() {
  const sel = document.getElementById('cross-up-to');
  const prev = sel.value;
  sel.innerHTML = '';
  const meets = STATE?.meets || [];
  meets.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.meet_number;
    opt.textContent = `Meet ${m.meet_number}${m.is_locked ? ' 🔒' : ''}`;
    sel.appendChild(opt);
  });
  if (prev && sel.querySelector(`option[value="${prev}"]`)) sel.value = prev;
  renderCrossRef();
}

function renderCrossRef() {
  const wrap = document.getElementById('cross-table-wrap');
  if (!STATE?.meets?.length) {
    wrap.innerHTML = '<p style="color:var(--ink-2)">No meets generated yet.</p>';
    return;
  }

  const upTo = +document.getElementById('cross-up-to').value || Infinity;

  // Build frequency map using team names as stable identifiers
  const freq = {};
  const allTeamNames = new Set();

  STATE.meets.filter(m => m.meet_number <= upTo).forEach(m => {
    m.rooms.forEach(r => {
      const names = r.team_names;
      for (let i = 0; i < 3; i++) {
        allTeamNames.add(names[i]);
        for (let j = i + 1; j < 3; j++) {
          const t1 = names[i];
          const t2 = names[j];
          const key = [t1, t2].sort().join('||');
          freq[key] = (freq[key] || 0) + 1;
        }
      }
    });
  });

  const sortedNames = [...allTeamNames].sort();
  const cnt = (n1, n2) => {
    const key = [n1, n2].sort().join('||');
    return freq[key] || 0;
  };

  // Header row
  let html = '<table class="cross-table"><thead><tr><th>Team</th>';
  sortedNames.forEach(name => { html += `<th title="${esc(name)}">${esc(abbr(name))}</th>`; });
  html += '<th class="sum-col">Σ</th></tr></thead><tbody>';

  sortedNames.forEach(n1 => {
    let rowSum = 0;
    let cells = '';
    sortedNames.forEach(n2 => {
      if (n1 === n2) { cells += '<td class="diag">—</td>'; return; }
      const c = cnt(n1, n2);
      rowSum += c;
      const cls = c === 0 ? 'cnt-0' : c === 1 ? 'cnt-1' : c === 2 ? 'cnt-2' : 'cnt-hi';
      cells += `<td class="${cls}">${c || ''}</td>`;
    });
    html += `<tr><td class="row-head">${esc(n1)}</td>${cells}<td class="sum-col">${rowSum}</td></tr>`;
  });

  html += '</tbody></table>';
  wrap.innerHTML = html;
}

function abbr(name) {
  // Abbreviate long team names for column headers
  if (name.length <= 6) return name;
  return name.split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 4) || name.slice(0,4);
}

// ── Utility ────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}
