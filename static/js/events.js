/* Shared helpers + the event log view. Loaded first; defines window.App. */
window.App = window.App || {};
(function (App) {
  App.state = { snap: null, selected: null, tab: 'explorer', paused: false };
  App.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  App.fmtT = function (ms) { return (ms / 1000).toFixed(3) + ' s'; };
  App.short = function (h, n) { return h ? h.slice(0, n || 8) : ''; };
  App.chainColor = function (hash) {
    return hash ? 'hsl(' + (parseInt(hash.slice(0, 6), 16) % 360) + ' 68% 64%)' : '#888';
  };
  App.$ = function (id) { return document.getElementById(id); };

  var TYPES = ['NODE_CREATED', 'NODE_REMOVED', 'NODE_STATUS', 'PEER_CONNECTED', 'PEER_DISCONNECTED', 'TX_CREATED', 'TX_BROADCAST',
    'TX_RECEIVED', 'TX_REJECTED', 'BLOCK_MINED', 'BLOCK_BROADCAST', 'BLOCK_RECEIVED', 'BLOCK_REJECTED', 'FORK_DETECTED',
    'CHAIN_REORG', 'CHAIN_SYNC', 'MINING_STARTED', 'MINING_STOPPED', 'MESSAGE_DROPPED', 'DIFFICULTY_CHANGED', 'LATENCY_CHANGED',
    'PARTITION', 'HEAL', 'SIM_PAUSED', 'SIM_RESUMED', 'SIM_RESET', 'SCENARIO', 'ERROR'];
  var MAX = 400;
  var log = [], filter = '', dirty = false;

  function render() {
    dirty = false;
    var ol = App.$('event-log');
    if (!ol) return;
    var shown = [];
    for (var i = log.length - 1; i >= 0 && shown.length < 300; i--) {
      if (!filter || log[i].type === filter) shown.push(log[i]);
    }
    ol.innerHTML = shown.map(function (e) {
      return '<li><span class="t">' + (e.time_ms / 1000).toFixed(2) + 's</span><span class="ty ' + e.type + '">' + e.type +
        '</span><span>' + App.esc(e.message) + '</span></li>';
    }).join('') || '<li class="muted"><span></span><span></span><span>No events yet.</span></li>';
  }

  App.Events = {
    init: function () {
      var sel = App.$('ev-filter');
      sel.innerHTML = '<option value="">all events</option>' + TYPES.map(function (t) { return '<option>' + t + '</option>'; }).join('');
      sel.onchange = function () { filter = sel.value; render(); };
      App.$('ev-clear').onclick = function () { log = []; render(); };
      render();
    },
    add: function (list, initial) {
      log = log.concat(list);
      if (log.length > MAX) log = log.slice(-MAX);
      if (App.state.tab === 'events') render(); else dirty = true;
      if (!initial && App.onEvents) App.onEvents(list);
    },
    clear: function () { log = []; render(); },
    show: function () { if (dirty) render(); }
  };
})(window.App);
