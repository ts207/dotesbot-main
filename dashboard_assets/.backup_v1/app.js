// AEGIS INSTITUTIONAL TERMINAL v6.0

document.addEventListener('DOMContentLoaded', () => {
    const clockEl = document.getElementById('clock');
    const statusDot = document.getElementById('status-dot');
    const statusLabel = document.getElementById('status-label');
    
    // KPI
    const kpiPnlVal = document.getElementById('kpi-pnl-val');
    const kpiLivePnlVal = document.getElementById('kpi-live-pnl-val');
    const kpiWinVal = document.getElementById('kpi-win-val');
    const kpiOpenVal = document.getElementById('kpi-open-val');
    const kpiNotionalVal = document.getElementById('kpi-notional-val');
    const healthStack = document.getElementById('health-stack');

    // Feeds
    const signalFeed = document.getElementById('signal-feed');
    const eventFeed = document.getElementById('event-feed');
    const mwFeed = document.getElementById('mw-feed');
    const rescueFeed = document.getElementById('rescue-feed');
    const attemptsBody = document.getElementById('attempts-body');
    const attemptsCount = document.getElementById('attempts-count');
    const openBody = document.getElementById('open-positions-list');
    const openCount = document.getElementById('open-count');
    const marketsList = document.getElementById('markets-list');
    const marketsCount = document.getElementById('markets-count');
    
    // Games
    const gamesList = document.getElementById('games-list');
    const gamesCount = document.getElementById('games-count');

    // Form
    const tradeForm = document.getElementById('trade-form');
    const tradeMsg = document.getElementById('trade-msg');
    const tokenInput = document.getElementById('f-token');
    const capInput = document.getElementById('f-cap');

    // State
    const nwHistory = {};

    // ── Tabs ──
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    tabBtns.forEach(btn => {
        btn.onclick = () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            tabContents.forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            const target = document.getElementById(btn.dataset.target);
            if (target) target.classList.add('active');
        };
    });

    // ── Size Presets ──
    document.querySelectorAll('.size-btn').forEach(btn => {
        btn.onclick = () => {
            document.getElementById('f-amount').value = btn.dataset.val;
            document.querySelectorAll('.size-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        };
    });

    // ── Hotkeys ──
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') {
            if (e.key === 'Escape') e.target.blur();
            return;
        }
        if (e.key >= '1' && e.key <= '4') {
            const idx = parseInt(e.key) - 1;
            if (tabBtns[idx]) tabBtns[idx].click();
        } else if (e.key === ' ' || e.key === 'Spacebar') {
            e.preventDefault();
            tokenInput.focus();
        }
    });

    // ── Clock ──
    setInterval(() => {
        const now = new Date();
        clockEl.textContent = now.toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
    }, 1000);

    // ── Helpers ──
    function formatTime(utcStr) {
        if (!utcStr) return '--:--:--';
        return utcStr.substring(11, 19);
    }
    
    function fmtAgeSec(s) { 
        if (s === null || s === undefined) return '—';
        if (s > 3600) return Math.round(s/3600) + 'h';
        if (s > 60) return Math.round(s/60) + 'm';
        return s + 's';
    }

    function setBotStatus(text, isOnline) {
        statusLabel.textContent = text;
        if (isOnline) {
            statusDot.className = 'status-dot active';
        } else {
            statusDot.className = 'status-dot offline';
        }
    }

    function setModeBadge(realMoney) {
        const el = document.getElementById('mode-badge');
        if (!el) return;
        if (realMoney === true) {
            el.textContent = 'LIVE $';
            el.style.background = 'var(--color-down)';
            el.style.color = '#fff';
        } else {
            el.textContent = 'PAPER';
            el.style.background = 'var(--color-warn)';
            el.style.color = '#000';
        }
    }

    function makePnlCurve(pts) {
        if (!pts || pts.length < 2) return '';
        const minVal = Math.min(...pts);
        const maxVal = Math.max(...pts);
        const range = maxVal - minVal;
        const scale = range === 0 ? 1 : range;
        const width = 80;
        const height = 30;
        const poly = pts.map((val, i) => {
            const x = (i / (pts.length - 1)) * width;
            const y = height - (((val - minVal) / scale) * height * 0.8) - (height * 0.1);
            return `${x},${y}`;
        }).join(' ');
        const latest = pts[pts.length-1];
        const color = latest >= 0 ? 'var(--color-up)' : 'var(--color-down)';
        return `<svg width="${width}" height="${height}" style="overflow:visible;"><polyline points="${poly}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    }

    function makeSparkline(pts) {
        if (!pts || pts.length < 2) return '';
        const maxAbs = Math.max(...pts.map(Math.abs));
        const scale = maxAbs === 0 ? 1 : maxAbs;
        const width = 120;
        const height = 40;
        const poly = pts.map((val, i) => {
            const x = (i / (pts.length - 1)) * width;
            const y = (height/2) - ((val / scale) * (height/2) * 0.8);
            return `${x},${y}`;
        }).join(' ');
        const latest = pts[pts.length-1];
        const color = latest > 0 ? 'var(--color-up)' : 'var(--color-down)';
        return `<svg width="${width}" height="${height}" style="overflow:visible;"><polyline points="${poly}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    }

    // ── Core Rendering ──
    function render(d) {
        try {
            _renderInternal(d);
        } catch (e) {
            console.error('Render Error:', e);
            if (statusLabel) statusLabel.textContent = 'RENDER_ERR';
            if (statusDot) statusDot.className = 'status-dot offline';
        }
    }

    function _renderInternal(d) {
        if (!d) return;
        const mappedMarkets = d.mapped_markets || {};

        // 1. KPIs
        const stats = d.stats || {};
        const pnl = stats.total_pnl || 0;
        if (kpiPnlVal) {
            kpiPnlVal.textContent = `${pnl>=0?'+':''}$${pnl.toFixed(2)}`;
            kpiPnlVal.className = 'kpi-value ' + (pnl>=0 ? 'val-up' : 'val-down');
        }
        
        const pnlCurveEl = document.getElementById('pnl-curve');
        if (pnlCurveEl && stats.pnl_history) {
            pnlCurveEl.innerHTML = makePnlCurve(stats.pnl_history);
        }

        const livePnl = (d.live_state && d.live_state.daily_realized_pnl_usd) || 0;
        if (kpiLivePnlVal) {
            kpiLivePnlVal.textContent = `${livePnl>=0?'+':''}$${livePnl.toFixed(2)}`;
            kpiLivePnlVal.className = 'kpi-value ' + (livePnl>=0 ? 'val-up' : 'val-down');
        }
        
        if (kpiWinVal) kpiWinVal.textContent = stats.win_rate ? stats.win_rate.toFixed(1) + '%' : '—';
        if (kpiOpenVal) kpiOpenVal.textContent = stats.open_count || 0;
        if (kpiNotionalVal) kpiNotionalVal.textContent = '$' + (stats.notional_usd || 0).toFixed(0);

        // Update Title
        const openPosCount = (d.live_positions && d.live_positions.length > 0) 
            ? d.live_positions.filter(p => p.state !== 'CLOSED').length 
            : (d.open_positions || []).length;
        document.title = `[${livePnl>=0?'+':''}$${livePnl.toFixed(2)} | ${openPosCount} Open] AEGIS`;

        // 2. Health Strip
        let hItems = d.health.items || [];
        if (d.live && d.live.usdc_balance !== null) {
            let label = 'WALLET(LOG)';
            if (d.live.is_live_on_chain) label = 'ON-CHAIN';
            else if (d.live.is_mock_balance) label = 'MOCK BAL';
            hItems.unshift({
                status: d.live.usdc_balance_age_sec < 300 ? 'fresh' : 'stale',
                label: label,
                age_sec: d.live.usdc_balance_age_sec,
                count: '$'+d.live.usdc_balance.toFixed(2)
            });
        }
        if (healthStack) {
            healthStack.innerHTML = hItems.map(h => `
                <div class="kpi-item" style="border-left: 1px solid var(--border-color); padding-left: 16px;">
                    <div class="kpi-label" style="display:flex; justify-content:space-between; width:100%;">
                        <span>${h.label}</span>
                        <span style="color:${h.status==='fresh'?'var(--color-up)':'var(--color-warn)'}">${fmtAgeSec(h.age_sec)}</span>
                    </div>
                    <div class="kpi-value">${h.count}</div>
                </div>
            `).join('');
        }

        // 3. Live Matches (ULTRA SPOTLIGHT)
        const games = d.games || [];
        if (gamesCount) gamesCount.textContent = games.length;
        if (!games.length) {
            if (gamesList) gamesList.innerHTML = '<div class="empty-msg" style="font-size: 24px; margin-top: 150px; color: var(--text-dim); letter-spacing: 2px;">SCANNING FOR LIVE COMBAT DEPLOYMENTS...</div>';
        } else {
            if (gamesList) gamesList.innerHTML = games.map(g => {
                const nwDiff = parseInt(g.net_worth_diff || 0);
                
                // Sparkline
                if (!nwHistory[g.match_id]) nwHistory[g.match_id] = [];
                nwHistory[g.match_id].push(nwDiff);
                if (nwHistory[g.match_id].length > 40) nwHistory[g.match_id].shift();

                const nwClass = nwDiff > 0 ? 'text-up' : nwDiff < 0 ? 'text-down' : 'text-muted';
                const gameTime = Math.floor(parseInt(g.game_time_sec || 0) / 60) + ':' + (parseInt(g.game_time_sec || 0) % 60).toString().padStart(2, '0');
                
                const linked = mappedMarkets[g.match_id] || [];
                const marketHtml = linked.map(m => `
                    <div class="market-buttons">
                        <button class="btn-market yes clickable-token" data-token="${m.yes_token_id}">
                            <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:80%; text-align:left;">BUY ${m.yes_team}</span>
                            <span>YES</span>
                        </button>
                        <button class="btn-market no clickable-token" data-token="${m.no_token_id}">
                            <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:80%; text-align:left;">BUY ${m.no_team}</span>
                            <span>NO</span>
                        </button>
                    </div>
                `).join('');

                const radPlayers = (g.players || []).filter(p => p.team === 0);
                const direPlayers = (g.players || []).filter(p => p.team === 1);

                const makeDraftHtml = (players) => players.map(p => `
                    <div class="draft-hero">
                        <span style="color:var(--text-main); font-weight:800;">${p.hero_name}</span>
                        <span class="${p.team===0?'text-up':'text-down'}" style="font-family:var(--font-mono);">${p.net_worth ? (p.net_worth/1000).toFixed(1)+'k' : '—'}</span>
                    </div>
                `).join('') || '<div class="text-dim">PICKING...</div>';

                const playerList = (g.players || []).map(p => {
                    const teamCls = p.team === 0 ? 'team-radiant' : 'team-dire';
                    const kda = p.kills !== undefined ? `${p.kills}/${p.deaths}/${p.assists}` : '—';
                    const nw = p.net_worth ? `${(p.net_worth/1000).toFixed(1)}k` : '—';
                    return `
                    <div class="player-row">
                        <span><span class="team-indicator ${teamCls}"></span><strong>${p.hero_name}</strong></span>
                        <span class="text-muted" style="width: 100px; overflow:hidden; text-overflow:ellipsis;">${p.name || ''}</span>
                        <span style="width: 60px; text-align:right;">${kda}</span>
                        <span class="text-up" style="width: 50px; text-align:right;">${nw}</span>
                    </div>`;
                }).join('');

                return `
                <div class="game-block" onclick="this.querySelector('.player-details').classList.toggle('active')">
                    <div class="game-header">
                        <span class="text-info" style="font-size: 28px; letter-spacing:-1px;">${g.radiant_team || 'Radiant'} vs ${g.dire_team || 'Dire'}</span>
                        <span class="text-muted" style="font-size: 16px;">${gameTime}</span>
                    </div>
                    
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 24px; padding: 12px; background: rgba(255,255,255,0.02); border-radius: 8px;">
                        <div style="display:flex; flex-direction:column; gap:6px;">
                            <div style="font-size: 48px; font-weight: 900; font-family: var(--font-mono); line-height: 1;">${g.radiant_score} — ${g.dire_score}</div>
                            <div style="font-size: 12px; color: var(--text-dim); text-transform:uppercase; letter-spacing:2px; font-weight:800;">Scoreboard / Kills</div>
                        </div>
                        
                        <div style="display:flex; align-items:center; gap:32px;">
                            ${makeSparkline(nwHistory[g.match_id])}
                            <div style="text-align:right;">
                                <div class="${nwClass}" style="font-size: 48px; font-weight: 900; font-family: var(--font-mono); line-height: 1;">${nwDiff > 0 ? '+' : ''}${Math.round(nwDiff/100)/10}k</div>
                                <div style="font-size: 12px; color: var(--text-dim); text-transform:uppercase; letter-spacing:2px; font-weight:800;">Net Worth Advantage</div>
                            </div>
                        </div>
                    </div>

                    <div style="font-size: 12px; font-weight: 900; color: var(--color-info); text-transform: uppercase; margin-bottom: 8px; letter-spacing: 1px;">Combat Roster // Draft</div>
                    <div class="draft-container" style="background: rgba(0,0,0,0.5); padding: 16px; border: 1px solid var(--border-color);">
                        <div class="draft-side" style="gap: 8px;">${makeDraftHtml(radPlayers)}</div>
                        <div class="draft-vs" style="font-size: 32px; opacity: 0.1;">VS</div>
                        <div class="draft-side" style="gap: 8px;">${makeDraftHtml(direPlayers)}</div>
                    </div>

                    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:20px;">
                         <div style="font-size: 20px; font-weight:900; color:var(--color-warn);">TOWERS ALIVE: ${g.radiant_towers} - ${g.dire_towers}</div>
                         <div style="font-size: 12px; color:var(--text-dim); font-family:var(--font-mono);">SYNC_ID: ${g.match_id}</div>
                    </div>

                    ${marketHtml || '<div class="text-muted" style="font-size:12px; margin-top: 16px; padding: 16px; border: 2px dashed var(--border-color); border-radius: 6px; text-align:center; font-weight:900; letter-spacing:1px; background:rgba(255,255,255,0.02);">SCANNING FOR LIVE POLYMARKET TOKENS...</div>'}
                    
                    <div class="player-details">
                        <div style="font-size: 12px; text-transform: uppercase; color: var(--color-info); margin-bottom: 12px; font-weight:900; border-bottom:2px solid var(--color-info); padding-bottom:6px; letter-spacing:1px;">Tactical Telemetry Depth</div>
                        ${playerList || '<div class="text-dim">Enriching combat stats...</div>'}
                    </div>
                </div>`;
            }).join('');
        }

        // 4. Order Books (L1)
        const mkts = d.prices || [];
        if (marketsCount) marketsCount.textContent = mkts.length;
        if (!mkts.length) {
            if (marketsList) marketsList.innerHTML = '<tr><td class="empty-msg">No liquidity data</td></tr>';
        } else {
            if (marketsList) marketsList.innerHTML = mkts.map(p => {
                const sideClass = p.side === 'YES' ? 'text-up' : 'text-down';
                const spread = (parseFloat(p.spread)*100).toFixed(1);
                return `
                <tr class="clickable-token" data-token="${p.token_id || ''}" data-cap="${p.ask}">
                    <td>
                        <div style="width: 100px; overflow: hidden; text-overflow: ellipsis;" title="${p.market}">${p.market}</div>
                        <span class="${sideClass}" style="font-size:9px; font-weight:700;">${p.side}</span>
                    </td>
                    <td class="text-right" style="color:var(--text-main); font-weight:600;">${parseFloat(p.ask).toFixed(3)}</td>
                    <td class="text-right text-info">${spread}¢</td>
                </tr>`;
            }).join('');
        }

        // 5. Signal Feed
        const sigs = d.signals || [];
        if (!sigs.length) {
            if (signalFeed) signalFeed.innerHTML = '<tr><td colspan="3" class="empty-msg">Waiting</td></tr>';
        } else {
            if (signalFeed) signalFeed.innerHTML = sigs.map(s => {
                const edge = ((parseFloat(s.executable_edge)||0)*100).toFixed(1);
                const ask = parseFloat(s.ask || s.best_ask || 0.99);
                const et = (s.event_type || 'UNKNOWN').replace('POLL_','');
                return `
                <tr class="clickable-signal" data-token="${s.token_id || ''}" data-cap="${(ask + 0.02).toFixed(2)}">
                    <td class="text-muted">${formatTime(s.timestamp_utc)}</td>
                    <td class="text-warn" title="${s.event_type || ''}">${et}</td>
                    <td class="text-right text-up">${edge}%</td>
                </tr>`;
            }).join('');
        }

        // 6. Events
        const evts = d.events || [];
        if (!evts.length) {
            if (eventFeed) eventFeed.innerHTML = '<tr><td class="empty-msg">None</td></tr>';
        } else {
            if (eventFeed) eventFeed.innerHTML = evts.map(e => {
                const mid = String(e.match_id || '').substring(0,8);
                return `
                <tr>
                    <td class="text-muted">${formatTime(e.timestamp_utc)}</td>
                    <td>${e.event_type || 'EVT'}</td>
                    <td class="text-right">${mid}</td>
                </tr>
            `;}).join('');
        }

        // 7. Match Winner
        const mws = d.match_winner || [];
        if (!mws.length) {
            if (mwFeed) mwFeed.innerHTML = '<tr><td class="empty-msg">None</td></tr>';
        } else {
            if (mwFeed) mwFeed.innerHTML = mws.map(m => {
                const edge = ((parseFloat(m.executable_edge||0))*100).toFixed(1);
                const ask = parseFloat(m.ask || m.best_ask || 0.99);
                return `
                <tr class="clickable-signal" data-token="${m.token_id || ''}" data-cap="${(ask + 0.02).toFixed(2)}">
                    <td class="text-muted">${formatTime(m.timestamp_utc)}</td>
                    <td class="text-info">${m.event_type || 'MW'}</td>
                    <td class="text-right text-up">${edge}%</td>
                </tr>
            `;}).join('');
        }

        // 9. Signal Decisions (event engine: what the bot wanted to do)
        const decisions = d.signal_decisions || [];
        if (attemptsCount) attemptsCount.textContent = decisions.length;
        if (!decisions.length) {
            if (attemptsBody) attemptsBody.innerHTML = '<tr><td colspan="3" class="empty-msg">Idle</td></tr>';
        } else {
            if (attemptsBody) attemptsBody.innerHTML = decisions.map(s => {
                const decision = (s.decision || 'pending').toLowerCase();
                const isSkip = decision === 'skip';
                const dClass = decision === 'submit' ? 'text-up' : isSkip ? 'text-down' : 'text-warn';
                const tag = isSkip ? (s.skip_reason || 'skip') : decision.toUpperCase();
                const et = (s.event_type || '').replace('POLL_', '').slice(0, 14);
                return `
                <tr>
                    <td class="text-muted" style="font-size:10px;">${formatTime(s.timestamp_utc)}</td>
                    <td style="font-weight:600; font-size:10px;">${et}</td>
                    <td class="${dClass}" style="font-weight:600; font-size:9px; text-align:right;">${tag.slice(0, 22)}</td>
                </tr>`;
            }).join('');
        }

        // 10. Open Positions
        const open = (d.live_positions && d.live_positions.length > 0) ? d.live_positions : d.open_positions || [];
        const activeOpen = open.filter(p => p.state !== 'CLOSED');
        if (openCount) openCount.textContent = activeOpen.length;
        if (!activeOpen.length) {
            if (openBody) openBody.innerHTML = '<tr><td colspan="4" class="empty-msg">None</td></tr>';
        } else {
            if (openBody) openBody.innerHTML = activeOpen.map(p => {
                const side = p.side || (p.token_id === p.yes_token_id ? 'YES' : 'NO');
                const sideClass = side === 'YES' ? 'text-up' : 'text-down';
                const tokenId = p.token_id || '';
                const matchId = p.match_id || '';
                return `
                <tr>
                    <td><div style="width: 110px; overflow: hidden; text-overflow: ellipsis; font-weight:700;">${p.market_name||p.token_id}</div></td>
                    <td class="${sideClass}" style="font-weight:700;">${side}</td>
                    <td class="text-right">${parseFloat(p.entry_price||0).toFixed(2)}</td>
                    <td class="text-right"><button class="exit-btn" data-token="${tokenId}" data-match="${matchId}" style="background:var(--color-down); color:#fff; border:none; padding:2px 8px; font-size:10px; font-weight:700; cursor:pointer; border-radius:2px;">EXIT</button></td>
                </tr>`;
            }).join('');

            // Wire EXIT buttons — POST /api/exit with confirmation
            document.querySelectorAll('.exit-btn').forEach(btn => {
                btn.onclick = async (e) => {
                    e.stopPropagation();
                    const tokenId = btn.dataset.token;
                    const matchId = btn.dataset.match;
                    if (!tokenId) { showTradeMsg('NO_TOKEN', 'text-down'); return; }
                    if (!confirm(`FAK exit ${tokenId.slice(0,8)}... on match ${matchId}?`)) return;
                    btn.disabled = true;
                    btn.textContent = '...';
                    try {
                        const res = await fetch('/api/exit', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ token_id: tokenId, match_id: matchId })
                        });
                        const j = await res.json();
                        if (j.status === 'queued') {
                            showTradeMsg(`EXIT QUEUED ${j.id.slice(0,8)}`, 'text-info');
                        } else {
                            showTradeMsg('EXIT ERR: ' + (j.error||'unknown'), 'text-down');
                            btn.disabled = false; btn.textContent = 'EXIT';
                        }
                    } catch (err) {
                        showTradeMsg('NET ERR', 'text-down');
                        btn.disabled = false; btn.textContent = 'EXIT';
                    }
                };
            });
        }

        // Bind clicks
        document.querySelectorAll('.clickable-token, .clickable-signal').forEach(el => {
            el.onclick = (e) => {
                tokenInput.value = el.dataset.token || '';
                if (e.shiftKey) {
                    if (!el.dataset.cap) {
                        showTradeMsg('NO_CAP', 'text-down');
                        return;
                    }
                    const cap = (parseFloat(el.dataset.cap) + 0.02).toFixed(2);
                    capInput.value = cap;
                    tokenInput.style.borderColor = 'var(--color-up)';
                    setTimeout(() => tokenInput.style.borderColor = 'var(--border-color)', 500);
                    showTradeMsg(`EXECUTING @ ${cap}`, 'text-up');
                    tradeForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
                } else {
                    if (el.dataset.cap) capInput.value = parseFloat(el.dataset.cap).toFixed(2);
                    tokenInput.style.borderColor = 'var(--color-info)';
                    setTimeout(() => tokenInput.style.borderColor = 'var(--border-color)', 500);
                }
            };
            
            el.ondblclick = (e) => {
                e.preventDefault();
                const token = el.dataset.token || '';
                if (token) {
                    navigator.clipboard.writeText(token).then(() => {
                        showTradeMsg('COPIED', 'text-info');
                    });
                }
            };
        });
    }

    // ── Trade Form ──
    tradeForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('trade-btn');
        const btnText = document.getElementById('trade-btn-text');
        const tokenId = tokenInput.value.trim();
        const amountUsd = parseFloat(document.getElementById('f-amount').value);
        const priceCap = parseFloat(capInput.value);

        if (!tokenId || isNaN(amountUsd) || isNaN(priceCap)) {
            showTradeMsg('INCOMPLETE DATA', 'text-down');
            return;
        }

        try {
            btn.disabled = true;
            btnText.textContent = 'EXECUTING...';
            tradeMsg.className = 'text-muted';

            const response = await fetch('/api/trade', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token_id: tokenId, amount_usd: amountUsd, price_cap: priceCap, order_type: 'FAK' })
            });

            const result = await response.json();
            if (result.status === 'success') {
                showTradeMsg('ORDER PLACED', 'text-up');
                tradeForm.reset();
            } else {
                showTradeMsg('ERROR: ' + (result.error || 'UNKNOWN'), 'text-down');
            }
        } catch (err) {
            showTradeMsg('SYSTEM EXCEPTION', 'text-down');
        } finally {
            btn.disabled = false;
            btnText.textContent = 'EXECUTE ACQUISITION';
        }
    });

    let msgTimeout = null;
    function showTradeMsg(msg, className) {
        tradeMsg.textContent = msg;
        tradeMsg.className = className;
        
        clearTimeout(msgTimeout);
        msgTimeout = setTimeout(() => {
            tradeMsg.textContent = '';
            tradeMsg.className = '';
        }, 5000);
    }

    // ── WebSocket ──
    let ws = null;
    let wsReconnectTimeout = null;

    function connectWS() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${window.location.host}/api/ws`);
        
        ws.onopen = () => setBotStatus('ACTIVE', true);
        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'update') {
                    render(data);
                    setBotStatus(data.health.mode === 'live' ? 'ACTIVE' : data.health.mode.toUpperCase(), true);
                    setModeBadge(data.health.real_money);
                }
            } catch (e) { console.error('WS error:', e); }
        };
        ws.onclose = () => {
            setBotStatus('OFFLINE', false);
            clearTimeout(wsReconnectTimeout);
            wsReconnectTimeout = setTimeout(connectWS, 2000);
        };
        ws.onerror = () => ws.close();
    }

    async function initialFetch() {
        try {
            const res = await fetch('/api/data?t=' + Date.now());
            const data = await res.json();
            render(data);
            setBotStatus(data.health.mode === 'live' ? 'ACTIVE' : data.health.mode.toUpperCase(), true);
            setModeBadge(data.health.mode);
        } catch (e) {
            setBotStatus('OFFLINE', false);
        }
    }

    initialFetch().then(connectWS);
});
