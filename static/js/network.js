/* D3 force-directed network graph. All state comes from the backend snapshot;
   particles are drawn from real simulated messages (sim:messages). */
(function (App) {
  var TYPE_COLOR = { TRANSACTION: '#f0b44c', BLOCK: '#4fd6a6', CHAIN_REQUEST: '#58a6ff', CHAIN_RESPONSE: '#7fb8ff',
    PEER_CONNECT: '#a78bfa', PEER_DISCONNECT: '#ff6b6b' };
  var svg, gLinks, gNodes, gParticles, simulation, tip, W = 800, H = 500;
  var nodes = [], links = [], byId = {}, topoKey = '', particles = 0;

  function size() {
    var el = document.getElementById('graph');
    W = el.clientWidth || 800; H = el.clientHeight || 500;
    if (simulation) {
      simulation.force('center', d3.forceCenter(W / 2, H / 2));
      simulation.force('x', d3.forceX(W / 2).strength(0.04));
      simulation.force('y', d3.forceY(H / 2).strength(0.06));
      simulation.alpha(0.3).restart();
    }
  }

  function showTip(evt, html) {
    var box = document.getElementById('graph').getBoundingClientRect();
    tip.html(html).classed('hidden', false)
      .style('left', Math.min(evt.clientX - box.left + 14, box.width - 230) + 'px')
      .style('top', (evt.clientY - box.top + 14) + 'px');
  }
  function hideTip() { tip.classed('hidden', true); }

  function nodeStyle(d) {
    var n = d.data;
    if (n.connection === 'offline') return { fill: '#2a3144', stroke: '#6b7694', dash: '5 4', opacity: 0.7 };
    if (n.connection === 'isolated') return { fill: App.chainColor(n.tip), stroke: '#f0b44c', dash: '5 4', opacity: 1 };
    return { fill: App.chainColor(n.tip), stroke: '#4fd6a6', dash: null, opacity: 1 };
  }

  function draw(maxH) {
    var link = gLinks.selectAll('g.lnk').data(links, function (d) { return d.id; });
    link.exit().remove();
    var enter = link.enter().append('g').attr('class', 'lnk');
    enter.append('line').attr('class', 'link');
    enter.append('line').attr('class', 'link-hit')
      .on('mousemove', function (e, d) {
        var a = typeof d.source === 'object' ? d.source.id : d.source, b = typeof d.target === 'object' ? d.target.id : d.target;
        showTip(e, '<b>' + a + ' &harr; ' + b + '</b><br>peer connection (' + d.state + ')<br>latency ~' + d.latency_ms + ' ms &plusmn; jitter');
      }).on('mouseleave', hideTip);
    var allLinks = enter.merge(link);
    allLinks.select('.link').classed('degraded', function (d) { return d.state !== 'up'; });

    var sel = gNodes.selectAll('g.node').data(nodes, function (d) { return d.id; });
    sel.exit().remove();
    var ne = sel.enter().append('g').attr('class', 'node')
      .call(d3.drag()
        .on('start', function (e, d) { if (!e.active) simulation.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
        .on('drag', function (e, d) { d.fx = e.x; d.fy = e.y; })
        .on('end', function (e) { if (!e.active) simulation.alphaTarget(0); }))
      .on('click', function (e, d) { App.select(d.id); })
      .on('dblclick', function (e, d) { d.fx = null; d.fy = null; simulation.alpha(0.3).restart(); })
      .on('mousemove', function (e, d) {
        var n = d.data;
        showTip(e, '<b>' + n.id + '</b> ' + n.connection + '<br>height ' + n.height + ', tip ' + n.tip_short + '<br>' + n.peers.length + ' peers, ' + n.mempool + ' pending tx' +
          '<br><span style="color:#8593b5">drag to move &middot; double-click to release</span>');
      }).on('mouseleave', hideTip);
    ne.append('circle').attr('class', 'halo').attr('r', 24).attr('fill', 'none').attr('stroke-width', 4).style('opacity', 0);
    ne.append('circle').attr('class', 'miner-ring').attr('r', 31);
    ne.append('circle').attr('class', 'body').attr('r', 22);
    ne.append('text').attr('class', 'hgt').attr('dy', 4);
    ne.append('text').attr('class', 'name').attr('dy', 42);
    ne.append('rect').attr('class', 'hbar-bg').attr('x', -22).attr('y', 48).attr('width', 44).attr('height', 4).attr('rx', 2).attr('fill', '#26324e');
    ne.append('rect').attr('class', 'hbar').attr('x', -22).attr('y', 48).attr('height', 4).attr('rx', 2);

    var all = ne.merge(sel);
    all.classed('selected', function (d) { return d.id === App.state.selected; });
    all.select('.body').each(function (d) {
      var s = nodeStyle(d);
      d3.select(this).attr('fill', s.fill).attr('stroke', s.stroke).attr('stroke-dasharray', s.dash).attr('fill-opacity', s.opacity);
    });
    all.select('.hgt').text(function (d) { return d.data.height; });
    all.select('.name').text(function (d) { return d.id; });
    all.select('.miner-ring').style('display', function (d) { return d.data.mining && d.data.status === 'online' ? null : 'none'; });
    all.select('.hbar').attr('width', function (d) { return maxH ? 44 * d.data.height / maxH : 44; })
      .attr('fill', function (d) { return d.data.height === maxH ? '#4fd6a6' : '#f0b44c'; });
  }

  function tick() {
    gLinks.selectAll('g.lnk').each(function (d) {
      var s = d.source, t = d.target; if (typeof s !== 'object' || typeof t !== 'object') return;
      d3.select(this).selectAll('line').attr('x1', s.x).attr('y1', s.y).attr('x2', t.x).attr('y2', t.y);
    });
    gNodes.selectAll('g.node').attr('transform', function (d) {
      d.x = Math.max(36, Math.min(W - 36, d.x)); d.y = Math.max(36, Math.min(H - 56, d.y));
      return 'translate(' + d.x + ',' + d.y + ')';
    });
  }

  App.Graph = {
    init: function () {
      svg = d3.select('#svg');
      gLinks = svg.append('g'); gNodes = svg.append('g'); gParticles = svg.append('g').style('pointer-events', 'none');
      tip = d3.select('#tooltip');
      simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(function (d) { return d.id; }).distance(125).strength(0.5))
        .force('charge', d3.forceManyBody().strength(-520))
        .force('collide', d3.forceCollide(40))
        .on('tick', tick);
      size();
      if (window.ResizeObserver) new ResizeObserver(size).observe(document.getElementById('graph'));
      document.getElementById('legend').innerHTML = [
        ['#4fd6a6', 'connected'], ['#f0b44c', 'isolated (no live peer)'], ['#6b7694', 'offline'], ['#f0b44c', 'dashed ring = mining'],
        ['#f0b44c', 'transaction'], ['#4fd6a6', 'block'], ['#58a6ff', 'chain request/response'], ['#a78bfa', 'handshake'],
        ['hsl(200 68% 64%)', 'node colour = chain tip (same colour = same chain)']
      ].map(function (l) { return '<span><i style="background:' + l[0] + '"></i>' + l[1] + '</span>'; }).join('');
    },

    update: function (snap) {
      var maxH = snap.overview.network_height, seen = {};
      snap.nodes.forEach(function (n) {
        seen[n.id] = true;
        var o = byId[n.id];
        if (!o) {
          o = byId[n.id] = { id: n.id, x: W / 2 + (Math.random() - 0.5) * 160, y: H / 2 + (Math.random() - 0.5) * 160 };
          nodes.push(o);
        }
        o.data = n;
      });
      for (var i = nodes.length - 1; i >= 0; i--) if (!seen[nodes[i].id]) { delete byId[nodes[i].id]; nodes.splice(i, 1); }
      var key = nodes.map(function (n) { return n.id; }).join(',') + '#' + snap.links.map(function (l) { return l.id; }).join(',');
      links = snap.links.map(function (l) { return { id: l.id, source: l.source, target: l.target, latency_ms: l.latency_ms, state: l.state }; });
      if (key !== topoKey) {
        topoKey = key;
        simulation.nodes(nodes);
        simulation.force('link').links(links);
        simulation.alpha(0.6).restart();
      } else {
        // keep resolved link objects so line positions keep updating
        var cur = gLinks.selectAll('g.lnk').data();
        var map = {}; cur.forEach(function (d) { map[d.id] = d; });
        links = links.map(function (l) { var c = map[l.id]; if (c) { c.state = l.state; c.latency_ms = l.latency_ms; return c; } return l; });
      }
      draw(maxH);
    },

    refreshSelection: function () { gNodes.selectAll('g.node').classed('selected', function (d) { return d.id === App.state.selected; }); },

    flash: function (id, color) {
      if (!byId[id]) return;
      gNodes.selectAll('g.node').filter(function (d) { return d.id === id; }).select('.halo')
        .interrupt().attr('stroke', color).attr('r', 24).style('opacity', 1)
        .transition().duration(900).attr('r', 46).style('opacity', 0);
    },

    particles: function (msgs, speed) {
      msgs.forEach(function (m) {
        var a = byId[m.from], b = byId[m.to];
        if (!a || !b || particles > 250) return;
        particles++;
        var dur = Math.max(250, Math.min(8000, m.latency_ms / (speed || 1)));
        var big = m.type === 'BLOCK' || m.type === 'CHAIN_RESPONSE';
        gParticles.append('circle').attr('r', big ? 6 : 4).attr('fill', TYPE_COLOR[m.type] || '#fff')
          .attr('stroke', '#0b1020').attr('stroke-width', 1).attr('cx', a.x).attr('cy', a.y)
          .transition().duration(dur).ease(d3.easeLinear)
          .attrTween('cx', function () { return function (t) { return a.x + (b.x - a.x) * t; }; })
          .attrTween('cy', function () { return function (t) { return a.y + (b.y - a.y) * t; }; })
          .on('end', function () { particles--; d3.select(this).remove(); })
          .on('interrupt', function () { particles--; d3.select(this).remove(); });
      });
    }
  };
})(window.App);
