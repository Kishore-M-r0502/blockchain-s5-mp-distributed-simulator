/* Bootstrap: Socket.IO wiring, REST helper and every control. */
(function (App) {
  var $ = App.$, toastTimer = null, socket;

  App.toast = function (msg, isError) {
    var t = $('toast');
    t.textContent = msg; t.className = 'toast' + (isError ? ' error' : '');
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.add('hidden'); }, 4500);
  };

  function api(path, method, body) {
    return fetch('/api' + path, { method: method || 'GET', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, data: j }; }); })
      .then(function (res) { if (!res.ok) { App.toast(res.data.error || 'Request failed', true); return null; } return res.data; })
      .catch(function (e) { App.toast('Request failed: ' + e.message, true); return null; });
  }
  App.api = api;

  App.select = function (id) {
    App.state.selected = id;
    if (id && $('e-node').value !== id) { $('e-node').value = id; }
    App.Graph.refreshSelection();
    if (App.state.snap) App.Dashboard.inspector(App.state.snap);
    App.Dashboard.loadDetail(true);
    App.Chain.refresh(true);
  };

  function needNode() {
    if (!App.state.selected) { App.toast('Select a node in the graph first.', true); return null; }
    return App.state.selected;
  }

  var FLASH = { TX_RECEIVED: '#f0b44c', TX_CREATED: '#f0b44c', BLOCK_RECEIVED: '#4fd6a6', BLOCK_MINED: '#4fd6a6',
    BLOCK_REJECTED: '#ff6b6b', TX_REJECTED: '#ff6b6b', CHAIN_REORG: '#58a6ff', FORK_DETECTED: '#58a6ff' };
  App.onEvents = function (list) {
    list.slice(-60).forEach(function (e) { if (e.node && FLASH[e.type]) App.Graph.flash(e.node, FLASH[e.type]); });
  };

  function onSnapshot(snap) {
    App.state.snap = snap;
    App.Graph.update(snap);
    App.Dashboard.render(snap);
    App.Chain.drawForks(snap.fork, snap.nodes);
    if (App.state.tab === 'explorer') App.Chain.refresh(false);
  }

  function showTab(name) {
    App.state.tab = name;
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) { t.classList.toggle('active', t.getAttribute('data-tab') === name); });
    Array.prototype.forEach.call(document.querySelectorAll('.tabpane'), function (p) { p.classList.toggle('hidden', p.id !== 'tab-' + name); });
    if (name === 'explorer') App.Chain.refresh(true);
    if (name === 'txs') App.Dashboard.loadTx(true);
    if (name === 'mining' && App.state.snap) App.Dashboard.miningTable(App.state.snap);
    if (name === 'events') App.Events.show();
  }

  function wire() {
    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) { t.onclick = function () { showTab(t.getAttribute('data-tab')); }; });
    var num = function (id) { return parseInt($(id).value, 10); };

    $('btn-pause').onclick = function () { api('/simulation/' + (App.state.snap && App.state.snap.sim.running ? 'pause' : 'resume'), 'POST'); };
    $('sel-speed').onchange = function () { api('/simulation/speed', 'POST', { speed: parseFloat($('sel-speed').value) }); };
    $('btn-reset').onclick = function () { api('/reset', 'POST', { seed: num('in-seed') || 0 }); };
    $('btn-scenario').onclick = function () {
      api('/scenarios/' + $('sel-scenario').value + '/run', 'POST', { seed: num('in-seed') || 0 }).then(function (r) { if (r) App.toast('Scenario started: ' + r.title); });
    };
    $('sel-scenario').onchange = function () { var s = App.scenarios[$('sel-scenario').selectedIndex]; if (s) $('sel-scenario').title = s.summary; };

    $('c-add').onclick = function () { api('/nodes', 'POST', {}).then(function (r) { if (r) App.toast(r.id + ' joined and peered with two nodes'); }); };
    $('c-remove').onclick = function () { var n = needNode(); if (n) api('/nodes/' + n, 'DELETE').then(function () { App.state.selected = null; }); };
    $('c-toggle').onclick = function () {
      var n = needNode(); if (!n) return;
      var node = App.state.snap.nodes.filter(function (x) { return x.id === n; })[0];
      api('/nodes/' + n + '/status', 'POST', { online: node.status !== 'online' });
    };
    $('c-connect').onclick = function () { api('/peers/connect', 'POST', { a: $('c-peer-a').value, b: $('c-peer-b').value }); };
    $('c-disconnect').onclick = function () { api('/peers/disconnect', 'POST', { a: $('c-peer-a').value, b: $('c-peer-b').value }); };
    $('c-partition').onclick = function () { api('/network/partition', 'POST', {}).then(function (r) { if (r) App.toast(r.links_cut + ' link(s) cut'); }); };
    $('c-heal').onclick = function () { api('/network/heal', 'POST', {}).then(function (r) { if (r) App.toast(r.links_restored + ' link(s) restored'); }); };
    $('c-set-latency').onclick = function () { api('/network/latency', 'POST', { latency_ms: num('c-latency'), jitter_ms: num('c-jitter') }); };

    function miningTarget() { return $('m-all').checked ? 'all' : needNode(); }
    $('m-start').onclick = function () { var n = miningTarget(); if (n) api('/mining/start', 'POST', { node: n }); };
    $('m-stop').onclick = function () { var n = miningTarget(); if (n) api('/mining/stop', 'POST', { node: n }); };
    $('m-one').onclick = function () { var n = miningTarget(); if (n) api('/mining/start', 'POST', { node: n, blocks: 1 }); };
    $('m-set-diff').onclick = function () {
      api('/mining/difficulty', 'POST', { difficulty: num('m-diff') }).then(function (r) { if (r) App.toast('Difficulty applies from height ' + r.from_height); });
    };

    $('t-send').onclick = function () {
      var body = { sender: $('t-sender').value, receiver: $('t-receiver').value, amount: num('t-amount'), node: $('t-node').value };
      if ($('t-nonce').value !== '') body.nonce = num('t-nonce');
      api('/transactions', 'POST', body).then(function (r) {
        if (!r) return;
        App.toast(r.accepted ? 'Transaction ' + r.tx.id.slice(0, 8) + ' accepted and broadcast' : 'Rejected by ' + body.node + ': ' + r.reason, !r.accepted);
        App.Dashboard.loadTx(true);
      });
    };

    $('x-inject').onclick = function () { var n = needNode(); if (n) api('/nodes/' + n + '/invalid-block', 'POST', { kind: $('x-kind').value }); };
    $('x-kind').onchange = function () { $('x-desc').textContent = App.kinds[$('x-kind').value] || ''; };
    $('s-save').onclick = function () { api('/simulation/save', 'POST', { name: $('s-name').value }).then(function (r) { if (r) App.toast('Saved (digest ' + r.digest.slice(0, 10) + ')'); }); };
    $('s-load').onclick = function () {
      api('/simulation/load', 'POST', { name: $('s-name').value }).then(function (r) { if (r) App.toast('Replayed saved run; digest matches ' + r.digest.slice(0, 10)); });
    };
  }

  function boot() {
    App.Events.init(); App.Chain.init(); App.Graph.init(); wire();

    api('/scenarios').then(function (list) {
      App.scenarios = list || [];
      $('sel-scenario').innerHTML = App.scenarios.map(function (s) { return '<option value="' + s.name + '">' + App.esc(s.title) + '</option>'; }).join('');
      if (App.scenarios[0]) $('sel-scenario').title = App.scenarios[0].summary;
    });
    api('/invalid-block-kinds').then(function (k) {
      App.kinds = k || {};
      $('x-kind').innerHTML = Object.keys(App.kinds).map(function (n) { return '<option>' + n + '</option>'; }).join('');
      $('x-desc').textContent = App.kinds[$('x-kind').value] || '';
    });
    api('/consensus').then(function (c) {
      if (!c) return;
      $('consensus-notes').innerHTML = '<p>Simplified, educational consensus. Where it differs from real networks:</p><ul>' +
        c.assumptions.map(function (a) { return '<li>' + App.esc(a) + '</li>'; }).join('') + '</ul>' +
        '<p class="muted">Block reward ' + c.block_reward + ', up to ' + c.max_txs_per_block + ' transactions per block. All times shown are simulated time.</p>';
    });

    socket = io();
    socket.on('connect', function () { $('conn').textContent = 'live'; $('conn').className = 'conn on'; });
    socket.on('disconnect', function () { $('conn').textContent = 'offline'; $('conn').className = 'conn off'; });
    socket.on('sim:snapshot', onSnapshot);
    socket.on('sim:events', function (p) { App.Events.add(p.events, p.initial); });
    socket.on('sim:messages', function (msgs) { App.Graph.particles(msgs, App.state.snap ? App.state.snap.sim.speed : 1); });
    socket.on('sim:tick', function (t) { App.Dashboard.clock(t); if (App.state.snap) { App.state.snap.sim.running = t.running; App.state.snap.sim.speed = t.speed; } });
    socket.on('sim:reset', function () { App.Events.clear(); App.state.selected = null; });
  }

  boot();
})(window.App);
