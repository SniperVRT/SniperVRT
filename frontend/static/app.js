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
        const [st, str, ev, rd, ls] = await Promise.all([
          api('/system/status'), api('/strategies'),
          api('/events?limit=50'),
          api('/governance/readiness').catch(() => null),
          api('/live/status'),
        ]);
        status.value = st;
        strategies.value = str.strategies;
        events.value = ev.events;
        readiness.value = rd;
        liveStatus.value = ls;
        lastRefresh.value = Date.now();
      } catch (e) { console.warn(e); }
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
      priceChart, backtestChart, paperChart,
      formatTs, formatNumber, refreshData, setActiveStrategy, quickBacktest,
      runBacktest, runTournament, paperStart, paperStop, paperTick, paperReset,
      runValidation, liveUnlock, liveRelock, emergency, viewReport, reloadSettings,
      JSON,
    };
  },
}).mount('#app');
