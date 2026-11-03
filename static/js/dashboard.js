/* Overview cards, node inspector, mining monitor, transaction explorer, header. */
(function (App) {
  var $ = App.$, esc = App.esc;
  var lastDetail = 0, lastTx = 0, detail = null, busyD = false, busyT = false, lastKey = '';

  function get(path) { return fetch('/api' + path).then(function (r) { return r.json(); }); }

  function cards(o) {
    var items = [
      ['Total nodes', o.total_nodes], ['Connected nodes', o.connected_nodes], ['Total blocks', o.total_blocks],
      ['Network height', o.network_height], ['Pending transactions', o.pending_txs], ['Active miners', o.active_miners],
      ['Messages exchanged', o.total_messages + (o.messages_dropped ? ' (' + o.messages_dropped + ' dropped)' : '')],
      ['Distinct chain tips', o.competing_tips, o.competing_tips > 1]
    ];
    $('overview').innerHTML = items.map(function (i) {
      return '<div class="card' + (i[2] ? ' warn' : '') + '"><div class="v">' + i[1] + '</div><div class="k">' + i[0] + '</div></div>';
    }).join('');
  }

  function fillSelect(sel, options, keepIfPossible) {
    var key = options.join('|');
    if (sel.getAttribute('data-key') === key) return;
    var prev = sel.value;
    sel.innerHTML = options.map(function (o) { return '<option>' + esc(o) + '</option>'; }).join('');
    sel.setAttribute('data-key', key);
    if (keepIfPossible && options.indexOf(prev) >= 0) sel.value = prev;
  }

  function selects(snap) {
    var ids = snap.nodes.map(function (n) { return n.id; });
    ['c-peer-a', 'c-peer-b', 't-node', 'e-node'].forEach(function (id) { fillSelect($(id), ids, true); });
    if (!$('c-peer-b').getAttribute('data-init') && ids.length > 1) { $('c-peer-b').selectedIndex = 1; $('c-peer-b').setAttribute('data-init', '1'); }
    var accts = snap.accounts.map(function (a) { return a.name; });
    fillSelect($('t-sender'), accts, true);
    fillSelect($('t-receiver'), accts, true);
    if (!$('t-receiver').getAttribute('data-init') && accts.length > 1) { $('t-receiver').selectedIndex = 1; $('t-receiver').setAttribute('data-init', '1'); }
  }

  function header(snap) {
    var s = snap.sim;
    $('h-seed').textContent = s.seed;
    $('h-time').textContent = App.fmtT(s.time_ms);
    $('h-digest').textContent = s.digest.slice(0, 16);
    $('btn-pause').textContent = s.running ? 'Pause' : 'Resume';
    if (document.activeElement !== $('sel-speed')) $('sel-speed').value = String(s.speed);
    if (document.activeElement !== $('c-latency')) $('c-latency').value = s.latency_ms;
    if (document.activeElement !== $('c-jitter')) $('c-jitter').value = s.jitter_ms;
    if (document.activeElement !== $('m-diff')) $('m-diff').value = s.difficulty;
    var box = $('scenario-box'), sc = s.scenario;
    if (sc) {
      box.classList.remove('hidden');
      box.innerHTML = '<b>' + esc(sc.title) + '</b> &mdash; ' + esc(sc.summary) + '<div class="bar"><i style="width:' +
        Math.round(100 * sc.step / sc.total) + '%"></i></div><span class="muted">' + (sc.done ? 'finished' : 'step ' + sc.step + ' of ' + sc.total) + '</span>';
    } else box.classList.add('hidden');
    var h = snap.nodes.length ? snap.nodes[0].miner.hashrate : 1000;
    $('m-hint').textContent = 'About ' + Math.pow(2, s.difficulty) + ' attempts per block; one node at ' + h + ' attempts/s needs ~' +
      (Math.pow(2, s.difficulty) / h).toFixed(1) + ' simulated seconds on average. Difficulty changes apply from height ' + (snap.overview.network_height + 1) + '.';
  }

  function connPill(n) {
    var cls = n.connection === 'connected' ? 'ok' : n.connection === 'isolated' ? 'warn' : 'bad';
    return '<span class="pill ' + cls + '">' + n.connection + '</span>';
  }

  function inspector(snap) {
    var el = $('inspector-body'), id = App.state.selected;
    var n = snap.nodes.filter(function (x) { return x.id === id; })[0];
    if (!n) { el.className = 'muted'; el.innerHTML = 'Click a node in the graph.'; App.state.selected = null; return; }
    el.className = '';
    var m = n.miner;
    var html = '<dl class="kv"><dt>node id</dt><dd class="mono">' + n.id + '</dd>' +
      '<dt>connection</dt><dd>' + connPill(n) + '</dd>' +
      '<dt>peers</dt><dd class="mono">' + (n.peers.join(', ') || 'none') + '</dd>' +
      '<dt>chain height</dt><dd class="mono">' + n.height + (n.behind ? ' <span class="pill warn">' + n.behind + ' behind</span>' : '') + '</dd>' +
      '<dt>latest block</dt><dd class="mono"><span class="swatch" style="background:' + App.chainColor(n.tip) + '"></span>' + n.tip_short + '</dd>' +
      '<dt>mempool</dt><dd class="mono">' + n.mempool + ' tx</dd>' +
      '<dt>mining</dt><dd>' + (m.active ? '<span class="pill warn">mining</span> ' : 'idle ') + '<span class="muted">' + m.hashrate + ' attempts/s</span></dd>' +
      '<dt>side branches</dt><dd class="mono">' + n.side_branches + '</dd>' +
      '<dt>messages</dt><dd class="mono">' + n.stats.sent + ' sent, ' + n.stats.received + ' received, ' + n.stats.duplicates + ' duplicate</dd>' +
      '<dt>rejected</dt><dd class="mono">' + n.stats.blocks_rejected + ' blocks, ' + n.stats.txs_rejected + ' txs</dd></dl>';
    if (detail && detail.id === id) {
      html += '<h3 style="margin-top:10px">Mempool</h3>' + (detail.mempool_txs.length ? '<table>' + detail.mempool_txs.map(function (t) {
        return '<tr><td>' + esc(t.sender_name) + ' &rarr; ' + esc(t.receiver_name) + '</td><td class="num">' + t.amount + '</td><td class="mono">' + t.id.slice(0, 8) + '</td></tr>';
      }).join('') + '</table>' : '<span class="muted">empty</span>');
      html += '<h3 style="margin-top:10px">Recent messages received</h3>' + (detail.recent_messages.length ? '<div class="table-wrap" style="max-height:150px"><table>' +
        detail.recent_messages.slice(-8).reverse().map(function (r) {
          return '<tr><td class="mono">' + r.type + '</td><td>' + r.from + '</td><td class="muted">' + esc(r.hint) + '</td></tr>';
        }).join('') + '</table></div>' : '<span class="muted">none yet</span>');
    }
    el.innerHTML = html;
    var online = n.status === 'online';
    $('c-toggle').textContent = online ? 'Take selected offline' : 'Bring selected online';
  }

  function loadDetail(force) {
    var id = App.state.selected, now = Date.now();
    if (!id || busyD || (!force && now - lastDetail < 1000)) return;
    busyD = true; lastDetail = now;
    get('/nodes/' + id).then(function (d) { busyD = false; if (!d.error) { detail = d; if (App.state.snap) inspector(App.state.snap); } })
      .catch(function () { busyD = false; });
  }

  function miningTable(snap) {
    var rows = snap.nodes.map(function (n) {
      var m = n.miner, pct = Math.min(100, Math.round(100 * m.job_attempts / m.expected_attempts));
      return '<tr><td class="mono">' + n.id + '</td><td>' + (m.active ? '<span class="pill warn">mining</span>' : '<span class="muted">idle</span>') + '</td>' +
        '<td class="num">' + m.hashrate + '</td><td class="num">' + m.difficulty + '</td>' +
        '<td class="num">' + (m.job_height == null ? '-' : m.job_height) + '</td>' +
        '<td class="num">' + m.job_nonce + '</td><td class="num">' + m.job_attempts + '</td>' +
        '<td><span class="bar-mini"><i style="width:' + pct + '%"></i></span></td>' +
        '<td class="num">' + m.total_attempts + '</td>' +
        '<td class="num">' + (m.last_duration_ms == null ? '-' : (m.last_duration_ms / 1000).toFixed(2) + ' s') + '</td>' +
        '<td class="num">' + m.blocks_mined + '</td></tr>';
    }).join('');
    $('mining-table').innerHTML = '<p class="hint">Durations are simulated seconds: attempts divided by the node\'s hash rate. The bar shows the current job\'s attempts against the expected number (2^difficulty).</p>' +
      '<div class="table-wrap"><table><tr><th>node</th><th>state</th><th class="num">attempts/s</th><th class="num">difficulty</th><th class="num">target height</th><th class="num">nonce</th>' +
      '<th class="num">job attempts</th><th>progress</th><th class="num">total attempts</th><th class="num">last block took</th><th class="num">blocks mined</th></tr>' + rows + '</table></div>';
  }

  function txTable(list, cols) {
    if (!list.length) return '<span class="muted">none</span>';
    return '<div class="table-wrap"><table><tr>' + cols.map(function (c) { return '<th>' + c[0] + '</th>'; }).join('') + '</tr>' +
      list.slice().reverse().map(function (t) { return '<tr>' + cols.map(function (c) { return '<td>' + c[1](t) + '</td>'; }).join('') + '</tr>'; }).join('') + '</table></div>';
  }

  function accounts(snap) {
    $('accounts').innerHTML = snap.accounts.map(function (a) {
      return '<div class="acct"><span class="muted">' + esc(a.name) + '</span><b>' + a.balance + '</b><span class="muted">next nonce ' + a.next_nonce + '</span></div>';
    }).join('');
  }

  function loadTx(force) {
    var now = Date.now();
    if (busyT || (!force && now - lastTx < 1500)) return;
    busyT = true; lastTx = now;
    get('/transactions').then(function (d) {
      busyT = false;
      var who = function (t) { return esc(t.sender_name) + ' &rarr; ' + esc(t.receiver_name); };
      var id = function (t) { return '<span class="mono">' + t.id.slice(0, 8) + '</span>'; };
      $('tx-pending').innerHTML = txTable(d.pending, [['tx', id], ['payment', who], ['amt', function (t) { return t.amount; }],
        ['reached', function (t) { return t.propagation + ' <span class="bar-mini"><i style="width:' + (100 * t.held_by.length / Math.max(1, d.nodes)) + '%"></i></span>'; }]]);
      $('tx-confirmed').innerHTML = txTable(d.confirmed, [['tx', id], ['payment', who], ['amt', function (t) { return t.amount; }],
        ['block', function (t) { return '#' + t.block; }], ['conf.', function (t) { return t.confirmations; }], ['nodes', function (t) { return t.agreement; }]]);
      $('tx-rejected').innerHTML = txTable(d.rejected, [['tx', id], ['payment', who], ['node', function (t) { return t.node; }],
        ['reason', function (t) { return '<span style="color:var(--bad)">' + esc(t.reason) + '</span>'; }]]);
    }).catch(function () { busyT = false; });
  }

  App.Dashboard = {
    render: function (snap) {
      cards(snap.overview); header(snap); selects(snap); inspector(snap); accounts(snap);
      if (App.state.tab === 'mining') miningTable(snap);
      if (App.state.tab === 'txs') loadTx(false);
      loadDetail(false);
    },
    miningTable: miningTable, loadTx: loadTx, loadDetail: loadDetail, inspector: inspector,
    clock: function (tick) {
      $('h-time').textContent = App.fmtT(tick.time_ms);
      $('btn-pause').textContent = tick.running ? 'Pause' : 'Resume';
    }
  };
})(window.App);
