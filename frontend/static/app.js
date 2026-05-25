const { createApp, ref, reactive, onMounted, onBeforeUnmount, watch, nextTick, computed } = Vue;

async function api(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${path}: ${text}`);
  }
  const ctype = res.headers.get('content-type') || '';
  return ctype.includes('json') ? res.json() : res.text();
}

createApp({
  setup() {
    const tabs = [
      { id: 'home', label: 'Home' },
      { id: 'data', label: 'Market Data' },
      { id: 'strategies', label: 'Strategies' },
      { id: 'backtest', label: 'Backtest' },
      { id: 'paper', label: 'Paper' },
      { id: 'intel', label: 'Intelligence' },
      { id: 'research', label: 'Research' },
      { id: 'memory', label: 'Memory' },
      { id: 'live', label: 'Live (LOCKED)' },
      { id: 'wallet', label: 'Wallet' },
      { id: 'governance', label: 'Governance' },
      { id: 'reports', label: 'Reports' },
      { id: 'settings', label: 'Settings' },
    ];
    const active = ref('home');
    const status = ref(null);
    const readiness = ref(null);
    const liveStatus = ref(null);
    const strategies = ref([]);
    const events = ref([]);
    const decisions = ref([]);
    const reports = ref([]);
    const reportBody = ref(null);
    const settings = ref(null);
    const council = ref(null);
    const backtestRuns = ref([]);
    const paperTrades = ref([]);
    const tournament = ref(null);
    // Phase 2
    const news = ref([]);
    const sentiment = ref([]);
    const micro = ref([]);
    const regimes = ref([]);
    const findings = ref([]);
    const edges = ref([]);
    const walkForwards = ref([]);
    const monteCarlos = ref([]);
    const memoryEntries = ref([]);
    const traces = ref([]);
    const scheduler = ref(null);
    const wf = reactive({ strategy_type: 'ema_trend', window_size: 500, step_size: 100, loading: false });
    const mc = reactive({ run_id: 1, n_samples: 2000, loading: false });
    const lastRefresh = ref(0);
    const lastRefreshLabel = computed(() => {
      if (!lastRefresh.value) return '—';
      return new Date(lastRefresh.value).toLocaleTimeString();
    });

    const bt = reactive({
      strategy_type: 'ensemble', limit: 2000, starting_equity: 10000,
      allow_short: true, loading: false, last: null,
    });
    const unlock = reactive({ reason: '', confirm: '' });

    const priceChart = ref(null);
    const backtestChart = ref(null);
    const paperChart = ref(null);
    let _priceChart, _backtestChart, _paperChart;

    function formatTs(ts) {
      if (!ts) return '—';
      return new Date(ts * 1000).toLocaleString();
    }
    function formatNumber(n, decimals = 0) {
      if (n === null || n === undefined) return '—';
      return Number(n).toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: Math.max(decimals, 2),
      });
    }

    async function refreshAll() {
      try {
        const [st, str, ev, rd, ls, sc] = await Promise.all([
          api('/system/status'), api('/strategies'),
          api('/events?limit=50'),
          api('/governance/readiness').catch(() => null),
          api('/live/status'),
          api('/intel/scheduler/status').catch(() => null),
        ]);
        status.value = st;
        strategies.value = str.strategies;
        events.value = ev.events;
        readiness.value = rd;
        liveStatus.value = ls;
        scheduler.value = sc;
        lastRefresh.value = Date.now();
      } catch (e) { console.warn(e); }
    }

    async function refreshIntel() {
      const [n, s, m, r] = await Promise.all([
        api('/intel/news?limit=60'),
        api('/intel/sentiment?limit=40'),
        api('/intel/micro?limit=40'),
        api('/intel/regime/history?limit=40'),
      ]);
      news.value = n.items;
      sentiment.value = s.snapshots;
      micro.value = m.signals;
      regimes.value = r.regimes;
    }

    async function refreshResearch() {
      const [f, e, w, mcr] = await Promise.all([
        api('/intel/findings?limit=50'),
        api('/intel/edges?limit=60'),
        api('/intel/walk-forward?limit=20'),
        api('/intel/monte-carlo?limit=20'),
      ]);
      findings.value = f.findings;
      edges.value = e.edges;
      walkForwards.value = w.runs;
      monteCarlos.value = mcr.runs;
    }

    async function refreshMemory() {
      const [m, t] = await Promise.all([
        api('/intel/memory?limit=80'),
        api('/intel/traces?limit=80'),
      ]);
      memoryEntries.value = m.entries;
      traces.value = t.traces;
    }

    async function refreshBacktestRuns() {
      const { runs } = await api('/backtest/runs?limit=25');
      backtestRuns.value = runs;
    }

    async function refreshPaperData() {
      const { trades } = await api('/paper/trades?limit=50');
      paperTrades.value = trades;
      const { points } = await api('/paper/equity-curve?limit=500');
      drawChart(paperChart, '_paperChart', points, p => p.equity, 'Equity ($)');
    }

    async function refreshGovernance() {
      const { decisions: d } = await api('/governance/decisions?limit=25');
      decisions.value = d;
    }

    async function refreshReports() {
      reports.value = (await api('/reports')).reports;
    }

    async function refreshSettings() {
      settings.value = await api('/settings');
    }

    async function loadPriceChart() {
      const { candles } = await api('/data/candles?limit=400');
      drawChart(priceChart, '_priceChart', candles, c => c.close, 'BTC close ($)');
    }

    function drawChart(refEl, slot, data, getY, label) {
      if (!refEl.value || !data || !data.length) return;
      const cfg = {
        type: 'line',
        data: {
          labels: data.map(d => new Date((d.ts || 0) * 1000).toLocaleString()),
          datasets: [{
            label, data: data.map(getY),
            borderColor: '#41b6ff', backgroundColor: 'rgba(65,182,255,.1)',
            fill: true, pointRadius: 0, tension: 0.2, borderWidth: 1.5,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          animation: false,
          scales: {
            x: { ticks: { color: '#8b96a5', maxTicksLimit: 8 }, grid: { color: 'rgba(255,255,255,.05)' } },
            y: { ticks: { color: '#8b96a5' }, grid: { color: 'rgba(255,255,255,.05)' } },
          },
          plugins: { legend: { labels: { color: '#8b96a5' } } },
        },
      };
      const existing = slot === '_priceChart' ? _priceChart : (slot === '_backtestChart' ? _backtestChart : _paperChart);
      if (existing) existing.destroy();
      const chart = new Chart(refEl.value, cfg);
      if (slot === '_priceChart') _priceChart = chart;
      else if (slot === '_backtestChart') _backtestChart = chart;
      else _paperChart = chart;
    }

    // Tab-specific data on switch
    watch(active, async (tab) => {
      await nextTick();
      if (tab === 'data') loadPriceChart();
      if (tab === 'backtest') refreshBacktestRuns();
      if (tab === 'paper') refreshPaperData();
      if (tab === 'governance') refreshGovernance();
      if (tab === 'reports') refreshReports();
      if (tab === 'settings') refreshSettings();
      if (tab === 'intel') refreshIntel();
      if (tab === 'research') refreshResearch();
      if (tab === 'memory') refreshMemory();
    });

    // Actions
    async function refreshData() {
      const r = await api('/data/refresh', { method: 'POST' });
      alert(`Data refreshed (${r.source}, +${r.inserted} new candles)`);
      await refreshAll(); await loadPriceChart();
    }
    async function setActiveStrategy(name) {
      await api('/paper/active-strategy', { method: 'POST', body: { name } });
      await refreshAll();
    }
    async function quickBacktest(name) {
      bt.strategy_type = name; active.value = 'backtest';
    }
    async function runBacktest() {
      bt.loading = true;
      try {
        const r = await api('/backtest/run', {
          method: 'POST',
          body: {
            strategy_type: bt.strategy_type,
            params: {},
            limit: bt.limit,
            starting_equity: bt.starting_equity,
            allow_short: bt.allow_short,
          },
        });
        bt.last = r;
        await nextTick();
        drawChart(backtestChart, '_backtestChart', r.equity_curve, p => p.equity, 'Equity ($)');
        await refreshBacktestRuns();
      } catch (e) { alert(e.message); }
      finally { bt.loading = false; }
    }
    async function runTournament() {
      const r = await api('/learning/tournament', { method: 'POST' });
      tournament.value = r;
    }
    async function paperStart() { await api('/paper/start', { method: 'POST' }); refreshAll(); }
    async function paperStop()  { await api('/paper/stop', { method: 'POST' }); refreshAll(); }
    async function paperTick()  { await api('/paper/tick', { method: 'POST' }); refreshAll(); refreshPaperData(); }
    async function paperReset() {
      if (!confirm('Reset paper account? This wipes all positions and trades.')) return;
      await api('/paper/reset', { method: 'POST' });
      refreshAll(); refreshPaperData();
    }
    async function runValidation() {
      council.value = await api('/governance/validate', { method: 'POST' });
      refreshGovernance(); refreshReports();
    }
    async function liveUnlock() {
      try {
        await api('/live/unlock', { method: 'POST', body: { reason: unlock.reason, confirm: unlock.confirm } });
        unlock.reason = ''; unlock.confirm = '';
        refreshAll();
      } catch (e) { alert(e.message); }
    }
    async function liveRelock() { await api('/live/relock', { method: 'POST' }); refreshAll(); }
    async function emergency() {
      if (!confirm('Trigger EMERGENCY SHUTDOWN? Stops paper runtime and re-locks live.')) return;
      await api('/live/emergency-shutdown', { method: 'POST' }); refreshAll();
    }
    async function viewReport(name) { reportBody.value = await api(`/reports/${name}`); }
    async function reloadSettings() {
      await api('/settings/reload', { method: 'POST' });
      refreshSettings();
    }

    // Phase 2 actions
    async function newsIngest() {
      const r = await api('/intel/news/ingest', { method: 'POST' });
      alert(`News: fetched ${r.fetched}, inserted ${r.inserted} from sources ${(r.sources || []).join(', ')}`);
      refreshIntel();
    }
    async function newsAttribute() { await api('/intel/news/attribute', { method: 'POST' }); refreshIntel(); }
    async function sentimentSnap() { await api('/intel/sentiment/snapshot', { method: 'POST' }); refreshIntel(); }
    async function microCollect()  { await api('/intel/micro/collect', { method: 'POST' }); refreshIntel(); }
    async function regimeSnap()    { await api('/intel/regime/snapshot', { method: 'POST' }); refreshIntel(); }
    async function agentsRunAll()  {
      const r = await api('/intel/agents/run-all', { method: 'POST' });
      refreshResearch();
      return r;
    }
    async function edgesDiscover() {
      const r = await api('/intel/edges/discover', { method: 'POST' });
      alert(`Edges tested ${r.tested}, accepted ${r.accepted}.`);
      refreshResearch();
    }
    async function runWalkForward() {
      wf.loading = true;
      try {
        await api('/intel/walk-forward/run', { method: 'POST', body: {
          strategy_type: wf.strategy_type, params: {},
          window_size: wf.window_size, step_size: wf.step_size,
          starting_equity: 10000,
        } });
        refreshResearch();
      } catch (e) { alert(e.message); }
      finally { wf.loading = false; }
    }
    async function runMonteCarlo() {
      mc.loading = true;
      try {
        await api('/intel/monte-carlo/run', { method: 'POST', body: {
          run_id: mc.run_id, n_samples: mc.n_samples,
        } });
        refreshResearch();
      } catch (e) { alert(e.message); }
      finally { mc.loading = false; }
    }
    async function schedStart() { await api('/intel/scheduler/start', { method: 'POST' }); refreshAll(); }
    async function schedStop()  { await api('/intel/scheduler/stop', { method: 'POST' }); refreshAll(); }
    async function schedForce(name) {
      await api(`/intel/scheduler/run/${name}`, { method: 'POST' });
      refreshAll();
      if (active.value === 'intel') refreshIntel();
      if (active.value === 'research') refreshResearch();
    }

    let timer;
    onMounted(async () => {
      await refreshAll();
      timer = setInterval(refreshAll, 4000);
    });
    onBeforeUnmount(() => clearInterval(timer));

    return {
      tabs, active, status, readiness, liveStatus, strategies, events, decisions,
      reports, reportBody, settings, council, backtestRuns, paperTrades, tournament,
      bt, unlock, lastRefreshLabel,
      news, sentiment, micro, regimes, findings, edges, walkForwards, monteCarlos,
      memoryEntries, traces, scheduler, wf, mc,
      priceChart, backtestChart, paperChart,
      formatTs, formatNumber, refreshData, setActiveStrategy, quickBacktest,
      runBacktest, runTournament, paperStart, paperStop, paperTick, paperReset,
      runValidation, liveUnlock, liveRelock, emergency, viewReport, reloadSettings,
      newsIngest, newsAttribute, sentimentSnap, microCollect, regimeSnap,
      agentsRunAll, edgesDiscover, runWalkForward, runMonteCarlo,
      schedStart, schedStop, schedForce,
      JSON,
    };
  },
}).mount('#app');
