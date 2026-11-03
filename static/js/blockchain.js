/* Blockchain explorer (per-node chains) and the network-wide fork view. */
(function (App) {
  var $ = App.$, esc = App.esc;
  var data = null, nodeId = null, selHash = null, lastFetch = 0, badIdx = -1, busy = false;

  function api(path, method) { return fetch('/api' + path, { method: method || 'GET' }).then(function (r) { return r.json(); }); }

  function txTable(txs) {
    return '<table><tr><th>from</th><th>to</th><th class="num">amount</th><th class="num">nonce</th></tr>' + txs.map(function (t) {
      return '<tr><td>' + esc(t.sender_name) + '</td><td>' + esc(t.receiver_name) + '</td><td class="num">' + t.amount +
        '</td><td class="num">' + t.nonce + '</td></tr>';
    }).join('') + '</table>';
  }

  function drawStrip() {
    var el = $('chain-strip');
    if (!data) { el.innerHTML = '<span class="muted">No node selected.</span>'; return; }
    var side = data.side_blocks || [];
    var rows = data.blocks.slice().reverse().map(function (b) { return chip(b, false); })
      .concat(side.length ? ['<div class="hint">Blocks on side branches (not part of this node\'s best chain)</div>'] : [])
      .concat(side.slice().reverse().map(function (b) { return chip(b, true); }));
    el.innerHTML = rows.join('');
    Array.prototype.forEach.call(el.querySelectorAll('.block-chip'), function (btn) {
      btn.onclick = function () { selHash = btn.getAttribute('data-hash'); drawStrip(); drawDetail(); };
    });
  }

  function chip(b, side) {
    var cls = 'block-chip' + (b.hash === selHash ? ' sel' : '') + (side ? ' side' : '') + (b.index === badIdx && !side ? ' bad' : '');
    return '<button class="' + cls + '" data-hash="' + b.hash + '"><b class="mono">#' + b.index + '</b>' +
      '<span><span class="mono">' + App.short(b.hash, 14) + '</span><br><span class="muted">' + esc(b.miner) + '</span></span>' +
      '<span class="muted">' + Math.max(0, b.transactions.length - (b.index ? 1 : 0)) + ' tx</span></button>';
  }

  function find(hash) {
    if (!data) return null;
    return data.blocks.concat(data.side_blocks || []).filter(function (b) { return b.hash === hash; })[0];
  }

  function drawDetail() {
    var el = $('block-detail'), b = find(selHash);
    if (!b) { el.innerHTML = 'Select a block to inspect it.'; el.className = 'block-detail muted'; return; }
    el.className = 'block-detail';
    el.innerHTML = '<dl class="kv"><dt>height</dt><dd class="mono">' + b.index + '</dd>' +
      '<dt>hash</dt><dd class="mono">' + b.hash + '</dd>' +
      '<dt>previous hash</dt><dd class="mono">' + b.previous_hash + '</dd>' +
      '<dt>merkle root</dt><dd class="mono">' + App.short(b.merkle_root, 24) + '...</dd>' +
      '<dt>nonce</dt><dd class="mono">' + b.nonce + '</dd>' +
      '<dt>difficulty</dt><dd>' + b.difficulty + ' leading zero bits</dd>' +
      '<dt>timestamp</dt><dd>' + App.fmtT(b.timestamp_ms) + ' <span class="muted">(simulated)</span></dd>' +
      '<dt>miner</dt><dd>' + esc(b.miner) + '</dd></dl>' +
      '<h3 style="margin-top:10px">Transactions (' + b.transactions.length + ')</h3>' + txTable(b.transactions) +
      '<div class="row"><button id="b-verify">Verify this block</button></div><div id="b-checks"></div>';
    $('b-verify').onclick = function () {
      api('/nodes/' + nodeId + '/blocks/' + b.index + '/verify').then(function (r) {
        if (r.error) { $('b-checks').innerHTML = '<span class="pill bad">' + esc(r.error) + '</span>'; return; }
        $('b-checks').innerHTML = '<table>' + r.checks.map(function (c) {
          return '<tr><td>' + (c.ok ? '<span class="pill ok">pass</span>' : '<span class="pill bad">fail</span>') + '</td><td>' + esc(c.check) + '</td></tr>';
        }).join('') + '</table>';
      });
    };
  }

  function refresh(force) {
    nodeId = $('e-node').value || App.state.selected;
    if (!nodeId) { data = null; drawStrip(); drawDetail(); return; }
    var now = Date.now();
    if (busy || (!force && now - lastFetch < 1200)) return;
    busy = true; lastFetch = now;
    api('/nodes/' + nodeId + '/chain').then(function (r) {
      busy = false;
      if (r.error) { data = null; } else { data = r; if (!selHash || !find(selHash)) selHash = r.tip; }
      drawStrip(); drawDetail();
    }).catch(function () { busy = false; });
  }

  // ----- fork view ----------------------------------------------------
  function drawForks(fork, nodes) {
    var host = $('fork-view');
    if (!fork || !fork.blocks.length) { host.innerHTML = ''; return; }
    var blocks = fork.blocks, min = fork.min_index, pos = {}, lanes = 0, childCount = {};
    blocks.forEach(function (b) {
      var p = pos[b.prev], lane;
      if (p && !childCount[b.prev]) lane = p.lane; else lane = lanes++;
      if (p) childCount[b.prev] = (childCount[b.prev] || 0) + 1;
      pos[b.hash] = { lane: lane, b: b };
    });
    var maxIdx = blocks[blocks.length - 1].index, W = (maxIdx - min + 1) * 74 + 90, H = Math.max(1, lanes) * 74 + 40;
    var tips = {};
    nodes.forEach(function (n) { (tips[n.tip] = tips[n.tip] || []).push(n.id.replace('node-', 'n')); });
    var svg = '<svg width="' + W + '" height="' + H + '" role="img" aria-label="Block tree">';
    blocks.forEach(function (b) {
      var p = pos[b.prev]; if (!p) return;
      var a = pos[b.hash], x1 = 40 + (p.b.index - min) * 74, y1 = 30 + p.lane * 74, x2 = 40 + (b.index - min) * 74, y2 = 30 + a.lane * 74;
      svg += '<path d="M' + x1 + ',' + y1 + ' C' + (x1 + 40) + ',' + y1 + ' ' + (x2 - 40) + ',' + y2 + ' ' + x2 + ',' + y2 + '" fill="none" stroke="#4a5a85" stroke-width="2"/>';
    });
    blocks.forEach(function (b) {
      var a = pos[b.hash], x = 40 + (b.index - min) * 74, y = 30 + a.lane * 74, best = b.best_on.length > 0;
      svg += '<g><title>#' + b.index + ' ' + b.hash + '\nminer: ' + esc(b.miner) + '\nknown by: ' + b.known_by.join(', ') + '\non best chain of: ' + (b.best_on.join(', ') || 'nobody') + '</title>' +
        '<circle cx="' + x + '" cy="' + y + '" r="14" fill="' + (best ? App.chainColor(b.hash) : 'none') + '" stroke="' + App.chainColor(b.hash) + '" stroke-width="2"' + (best ? '' : ' stroke-dasharray="3 3"') + '/>' +
        '<text x="' + x + '" y="' + (y + 4) + '" text-anchor="middle" font-family="IBM Plex Mono, monospace" font-size="11" fill="' + (best ? '#0b1020' : '#d6deef') + '">' + b.index + '</text>' +
        '<text x="' + x + '" y="' + (y + 29) + '" text-anchor="middle" font-size="10" fill="#8593b5">' + esc(b.miner) + '</text>' +
        (tips[b.hash] ? '<text x="' + x + '" y="' + (y - 20) + '" text-anchor="middle" font-size="10" fill="#f0b44c">tip: ' + tips[b.hash].join(' ') + '</text>' : '') + '</g>';
    });
    host.innerHTML = svg + '</svg>';
  }

  App.Chain = {
    init: function () {
      $('e-node').onchange = function () { selHash = null; refresh(true); };
      $('e-validate').onclick = function () {
        if (!nodeId) return;
        api('/nodes/' + nodeId + '/validate').then(function (r) {
          badIdx = r.valid ? -1 : r.bad_index;
          $('e-validation').className = 'pill ' + (r.valid ? 'ok' : 'bad');
          $('e-validation').textContent = r.valid ? 'chain valid (height ' + r.height + ')' : 'invalid at #' + r.bad_index + ': ' + r.error;
          drawStrip();
        });
      };
      $('e-tamper').onclick = function () {
        if (!nodeId) return;
        fetch('/api/nodes/' + nodeId + '/tamper', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ index: parseInt($('e-tamper-idx').value, 10) }) })
          .then(function (r) { return r.json(); }).then(function (r) {
            if (r.error) App.toast(r.error, true); else { App.toast('Block corrupted in ' + nodeId + '. Run "Verify whole chain".'); refresh(true); }
          });
      };
    },
    refresh: refresh,
    drawForks: drawForks
  };
})(window.App);
