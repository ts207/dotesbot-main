// Aegis v2 — operator + analytics

document.addEventListener('DOMContentLoaded', () => {

    // ── Tab switching ───────────────────────────────────
    document.querySelectorAll('.tab').forEach(b => {
        b.onclick = () => {
            document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(x => x.classList.remove('active'));
            b.classList.add('active');
            document.getElementById('tab-' + b.dataset.tab).classList.add('active');
        };
    });

    // ── Clock ───────────────────────────────────────────
    const clockEl = document.getElementById('clock');
    setInterval(() => {
        const now = new Date();
        clockEl.textContent = now.toISOString().substr(11, 8) + ' UTC';
    }, 1000);

    // ── Helpers ─────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const fmtTime = (ts) => ts ? new Date(ts).toISOString().substr(11, 8) : '—';
    const fmtNum = (n, d = 2) => (n == null || isNaN(n)) ? '—' : Number(n).toFixed(d);
    const fmtPnl = (n) => {
        if (n == null) return '$0.00';
        const v = Number(n);
        return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2);
    };
    const shortEvt = (e) => (e || '').replace('POLL_', '');

    function setStatus(text, online) {
        $('status-label').textContent = text;
        const pill = $('status-pill');
        pill.classList.remove('active', 'offline');
        if (online === true) pill.classList.add('active');
        else if (online === false) pill.classList.add('offline');
    }

    function setMode(realMoney) {
        const el = $('mode-badge');
        if (realMoney) {
            el.textContent = 'LIVE $';
            el.classList.add('live');
        } else {
            el.textContent = 'PAPER';
            el.classList.remove('live');
        }
    }

    // ── Render: Top KPIs ────────────────────────────────
    function renderKPIs(d) {
        const s = d.stats || {};
        const pnl = s.total_pnl || 0;
        const open = d.open_positions ? d.open_positions.length : 0;
        const wr = s.win_rate;
        $('kpi-pnl').textContent = fmtPnl(pnl);
        $('kpi-pnl').className = 'kpi-value ' + (pnl > 0 ? 'up' : pnl < 0 ? 'down' : '');
        $('kpi-open').textContent = open;
        $('kpi-wr').textContent = wr != null ? Math.round(wr * 100) + '%' : '—';
        $('kpi-games').textContent = (d.games || []).length;

        // Events/hr — derived from health
        const evRate = (d.health && d.health.events_per_hour) || null;
        $('kpi-rate').textContent = evRate ? Math.round(evRate) : '—';
    }

    // ── Render: Signal feed ─────────────────────────────
    function renderSignalFeed(rows) {
        const body = $('signal-feed');
        $('signal-count').textContent = rows.length;
        if (!rows.length) { body.innerHTML = '<tr><td colspan="3" class="empty">no signals</td></tr>'; return; }
        body.innerHTML = rows.slice(0, 25).map(r => {
            const dir = (r.direction || r.event_direction || '').toLowerCase();
            const dCls = dir === 'radiant' ? 'up' : dir === 'dire' ? 'down' : 'muted';
            return `<tr>
                <td class="muted mono">${fmtTime(r.timestamp_utc)}</td>
                <td>${shortEvt(r.event_type)}</td>
                <td class="right ${dCls} mono">${dir || '—'}</td>
            </tr>`;
        }).join('');
    }

    // ── Render: Signal Decisions ────────────────────────
    function renderDecisions(rows) {
        const body = $('decisions-body');
        $('decisions-count').textContent = rows.length;
        if (!rows.length) { body.innerHTML = '<tr><td colspan="3" class="empty">no decisions</td></tr>'; return; }
        body.innerHTML = rows.slice(0, 25).map(s => {
            const dec = (s.decision || '').toLowerCase();
            const isSkip = dec === 'skip';
            const tag = isSkip ? (s.skip_reason || 'skip') : dec.toUpperCase();
            const cls = dec === 'submit' ? 'up' : isSkip ? 'down' : 'warn';
            return `<tr>
                <td class="muted mono">${fmtTime(s.timestamp_utc)}</td>
                <td>${shortEvt(s.event_type)}</td>
                <td class="right ${cls} mono" style="font-size:10px;">${tag.slice(0, 26)}</td>
            </tr>`;
        }).join('');
    }

    // ── Render: Live games ──────────────────────────────
    function renderGames(games, prices, mapped) {
        const list = $('games-list');
        $('games-count').textContent = games.length;
        if (!games.length) {
            list.innerHTML = '<div class="empty">no live matches</div>';
            return;
        }
        const priceMap = {};
        (prices || []).forEach(p => { priceMap[p.token_id] = p; });

        list.innerHTML = games.map(g => {
            const teams = `${g.radiant_team || g.radiant_name || '?'} vs ${g.dire_team || g.dire_name || '?'}`;
            const gt = Number(g.game_time_sec || 0);
            const gtStr = `${Math.floor(gt/60)}:${String(gt%60).padStart(2,'0')}`;
            const lead = Number(g.net_worth_diff || 0);
            const leadStr = lead === 0 ? '0' : (lead > 0 ? `R+${lead.toLocaleString()}` : `D+${(-lead).toLocaleString()}`);
            const leadCls = lead > 0 ? 'up' : lead < 0 ? 'down' : '';
            const score = `${g.radiant_score || 0} - ${g.dire_score || 0}`;
            const mid = String(g.match_id || '');
            const mks = (mapped && mapped[mid]) || [];

            const priceChips = mks.slice(0, 3).map(m => {
                const yp = priceMap[m.yes_token_id];
                const np = priceMap[m.no_token_id];
                const yes = yp ? Number(yp.ask || yp.bid || 0).toFixed(2) : '—';
                const no  = np ? Number(np.ask || np.bid || 0).toFixed(2) : '—';
                return `
                <div class="price-chip clickable-token" data-token="${m.yes_token_id}" data-cap="${yes}" title="${m.name || ''}" style="cursor:pointer;">
                    <span class="team">${m.yes_team || 'YES'}</span>
                    <span class="price">${yes}</span>
                </div>
                <div class="price-chip clickable-token" data-token="${m.no_token_id}" data-cap="${no}" title="${m.name || ''}" style="cursor:pointer;">
                    <span class="team">${m.no_team || 'NO'}</span>
                    <span class="price">${no}</span>
                </div>`;
            }).join('');

            return `<div class="game-card">
                <div class="game-card-header">
                    <div class="game-teams">${teams}</div>
                    <div class="game-time">${gtStr}</div>
                </div>
                <div class="game-stats">
                    <div class="gs"><div class="gs-label">Score</div><div class="gs-value">${score}</div></div>
                    <div class="gs"><div class="gs-label">NW Lead</div><div class="gs-value ${leadCls}">${leadStr}</div></div>
                    <div class="gs"><div class="gs-label">Match ID</div><div class="gs-value muted">${mid.slice(-6)}</div></div>
                </div>
                ${priceChips ? `<div class="game-prices">${priceChips}</div>` : ''}
            </div>`;
        }).join('');

        // Wire clickable tokens
        document.querySelectorAll('.clickable-token').forEach(el => {
            el.onclick = (e) => {
                $('f-token').value = el.dataset.token || '';
                if (el.dataset.cap) $('f-cap').value = (Number(el.dataset.cap) + 0.02).toFixed(2);
                showTradeMsg('token loaded', 'info');
            };
        });
    }

    // ── Render: Open positions ──────────────────────────
    function renderPositions(open) {
        const body = $('open-positions-list');
        $('open-count').textContent = open.length;
        if (!open.length) {
            body.innerHTML = '<tr><td colspan="4" class="empty">no positions</td></tr>';
            return;
        }
        body.innerHTML = open.map(p => {
            const side = p.side || '—';
            const sCls = side === 'YES' ? 'up' : side === 'NO' ? 'down' : '';
            const name = (p.market_name || p.token_id || '').slice(0, 28);
            return `<tr>
                <td><div style="max-width:130px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${p.market_name||''}">${name}</div></td>
                <td class="${sCls} mono">${side}</td>
                <td class="right mono">${fmtNum(p.entry_price)}</td>
                <td class="right">
                    <button class="btn danger btn-xs exit-btn" data-token="${p.token_id||''}" data-match="${p.match_id||''}">EXIT</button>
                </td>
            </tr>`;
        }).join('');

        document.querySelectorAll('.exit-btn').forEach(btn => {
            btn.onclick = async () => {
                const tok = btn.dataset.token, mid = btn.dataset.match;
                if (!tok) return;
                if (!confirm(`FAK exit ${mid}?`)) return;
                btn.disabled = true; btn.textContent = '…';
                try {
                    const res = await fetch('/api/exit', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({token_id: tok, match_id: mid})
                    });
                    const j = await res.json();
                    showTradeMsg(j.status === 'queued' ? 'EXIT QUEUED' : 'ERR ' + (j.error||''),
                                 j.status === 'queued' ? 'up' : 'down');
                } catch (e) { showTradeMsg('network err', 'down'); }
            };
        });
    }

    // ── Render: Closed positions ────────────────────────
    function renderClosed(rows) {
        const body = $('closed-body');
        // Compute total realized PnL
        const totalPnl = rows.reduce((s, r) => s + Number(r.pnl_usd || 0), 0);
        const pnlCls = totalPnl > 0 ? 'up' : totalPnl < 0 ? 'down' : '';
        $('closed-count').innerHTML = rows.length > 0
            ? `<span class="${pnlCls}" style="font-family:var(--font-mono);">${fmtPnl(totalPnl)}</span> · ${rows.length}`
            : '0';
        if (!rows.length) { body.innerHTML = '<tr><td colspan="4" class="empty">none today</td></tr>'; return; }
        body.innerHTML = rows.slice(0, 12).map(c => {
            const pnl = Number(c.pnl_usd || 0);
            const cls = pnl > 0 ? 'up' : pnl < 0 ? 'down' : '';
            const name = (c.market_name || c.match_id || '').slice(0, 22);
            const side = c.side || '—';
            const sCls = side === 'YES' ? 'up' : side === 'NO' ? 'down' : '';
            const ts = fmtTime(c.timestamp_utc);
            const entry = c.entry_price != null ? Number(c.entry_price).toFixed(2) : '—';
            return `<tr>
                <td class="muted mono" style="font-size:10px;">${ts}</td>
                <td><div style="max-width:120px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${c.market_name||''}">${name}</div></td>
                <td class="${sCls} mono">${side} @${entry}</td>
                <td class="right ${cls} mono">${fmtPnl(pnl)}</td>
            </tr>`;
        }).join('');
    }

    // ── Render: Analytics tab ───────────────────────────
    function renderAnalytics(d) {
        const stats = d.stats || {};
        const ts = (d.ts || '').substr(0,19) + ' UTC';
        $('today-ts').textContent = ts;
        $('a-events').textContent = stats.total_events_today != null ? stats.total_events_today : (d.events || []).length;
        $('a-signals').textContent = stats.total_signals_today != null ? stats.total_signals_today : '—';
        $('a-entries').textContent = stats.total_entries || 0;
        $('a-pnl').textContent = fmtPnl(stats.total_pnl || 0);
        $('a-pnl').className = 'metric-value ' + ((stats.total_pnl||0) > 0 ? 'up' : (stats.total_pnl||0) < 0 ? 'down' : '');
        const wr = stats.win_rate;
        $('a-wr').textContent = wr != null ? Math.round(wr * 100) + '%' : '—';
        const ls = d.live_state || {};
        const dailyPnl = Number(ls.daily_realized_pnl_usd || 0);
        const cap = 200;
        const remaining = cap + dailyPnl;  // dailyPnl is negative if losing
        $('a-dd').textContent = '$' + remaining.toFixed(0);
        $('a-dd').className = 'metric-value ' + (remaining < 50 ? 'down' : '');
        $('a-dd-sub').textContent = `cap $${cap}`;

        // Per-event funnel from signal_decisions
        const decisions = d.signal_decisions || [];
        const byEvt = {};
        decisions.forEach(s => {
            const et = shortEvt(s.event_type) || 'unknown';
            if (!byEvt[et]) byEvt[et] = {submit: 0, skip: 0, total: 0};
            byEvt[et].total++;
            if (s.decision === 'submit') byEvt[et].submit++;
            else byEvt[et].skip++;
        });
        const evtRows = Object.entries(byEvt)
            .sort((a, b) => b[1].total - a[1].total)
            .map(([et, c]) => {
                const max = Math.max(...Object.values(byEvt).map(x => x.total));
                const w = Math.round((c.total / max) * 100);
                const submitPct = c.total ? Math.round((c.submit / c.total) * 100) : 0;
                const cls = submitPct > 0 ? 'submit' : 'skip';
                return `<div class="funnel-bar-row ${cls}">
                    <span class="label">${et}</span>
                    <span class="bar"><span class="bar-fill" style="width:${w}%"></span></span>
                    <span class="count">${c.submit}/${c.total}</span>
                </div>`;
            }).join('');
        $('per-event-funnel').innerHTML = evtRows || '<div class="empty">no signal decisions yet</div>';

        // Skip reasons
        const byRsn = {};
        decisions.filter(s => s.decision === 'skip').forEach(s => {
            const r = s.skip_reason || '(none)';
            byRsn[r] = (byRsn[r] || 0) + 1;
        });
        const rsnEntries = Object.entries(byRsn).sort((a,b) => b[1]-a[1]).slice(0, 12);
        const max = rsnEntries.length ? rsnEntries[0][1] : 1;
        const rsnRows = rsnEntries.map(([r, n]) => {
            const w = Math.round((n / max) * 100);
            return `<div class="funnel-bar-row skip">
                <span class="label" title="${r}">${r.slice(0, 30)}</span>
                <span class="bar"><span class="bar-fill" style="width:${w}%"></span></span>
                <span class="count">${n}</span>
            </div>`;
        }).join('');
        $('skip-reasons').innerHTML = rsnRows || '<div class="empty">no rejections yet</div>';
    }

    // ── Master render ───────────────────────────────────
    function render(d) {
        renderKPIs(d);
        renderSignalFeed(d.signals || []);
        renderDecisions(d.signal_decisions || []);
        renderGames(d.games || [], d.prices || [], d.mapped_markets || {});
        const open = (d.live_positions && d.live_positions.length) ? d.live_positions : (d.open_positions || []);
        renderPositions(open.filter(p => p.state !== 'CLOSED'));
        renderClosed(d.closed_positions || []);
        renderAnalytics(d);
        setMode(d.health && d.health.real_money);
    }

    // ── Manual trade form ───────────────────────────────
    function showTradeMsg(text, cls) {
        const el = $('trade-msg');
        el.textContent = text;
        el.className = cls === 'up' ? 'up' : cls === 'down' ? 'down' : cls === 'info' ? 'info' : 'muted';
        setTimeout(() => { if (el.textContent === text) el.textContent = ''; }, 3000);
    }
    $('trade-form').addEventListener('submit', async e => {
        e.preventDefault();
        const tok = $('f-token').value.trim();
        const amt = parseFloat($('f-amount').value);
        const cap = parseFloat($('f-cap').value);
        if (!tok || isNaN(amt) || isNaN(cap)) { showTradeMsg('incomplete', 'down'); return; }
        const btn = $('trade-btn'); btn.disabled = true;
        $('trade-btn-text').textContent = 'submitting…';
        try {
            const res = await fetch('/api/trade', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({token_id: tok, amount_usd: amt, price_cap: cap})
            });
            const j = await res.json();
            showTradeMsg(j.status === 'queued' ? `QUEUED ${j.id.slice(0,8)}` : 'ERR ' + (j.error||''),
                         j.status === 'queued' ? 'up' : 'down');
        } catch (e) { showTradeMsg('network err', 'down'); }
        finally { btn.disabled = false; $('trade-btn-text').textContent = 'Execute Buy (FAK)'; }
    });

    // ── Data fetch ──────────────────────────────────────
    let ws, wsTimeout;
    function connect() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${proto}//${location.host}/api/ws`);
        ws.onopen = () => setStatus('LIVE', true);
        ws.onmessage = (evt) => {
            try {
                const data = JSON.parse(evt.data);
                if (data.type === 'update') render(data);
            } catch (e) { console.error('WS parse error', e); }
        };
        ws.onclose = () => {
            setStatus('OFFLINE', false);
            clearTimeout(wsTimeout);
            wsTimeout = setTimeout(connect, 2000);
        };
        ws.onerror = () => ws.close();
    }
    async function initialFetch() {
        try {
            const res = await fetch('/api/data?t=' + Date.now());
            const data = await res.json();
            render(data);
            setStatus('LIVE', true);
        } catch (e) { setStatus('OFFLINE', false); }
    }
    initialFetch().then(connect);
});
