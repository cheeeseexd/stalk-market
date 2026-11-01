// Edge UI check using Playwright's bundled Node API.
const { chromium } = require(process.env.PLAYWRIGHT_PACKAGE);
const fs = require('fs');
const path = require('path');
const base = 'http://127.0.0.1:8099';
const state = JSON.parse(process.env.SIM_CHECK_STATE);
async function request(url, body) {
  const r = await fetch(base + url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  if (!r.ok) throw Error(`${url}: ${r.status}`);
  return r.json();
}
(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1600 }, deviceScaleFactor: 1 });
    const errors = [];
    page.on('pageerror', err => errors.push(String(err)));
    await page.goto(base + '/#week');
    await page.waitForSelector('#sim-candles svg .candle-up');
    for (const selector of ['#sim-candles .level.entry', '#sim-candles .level.stop', '#sim-candles path.fill-long']) {
      if (await page.locator(selector).count() !== 1) throw Error('Missing ' + selector);
    }
    const buttons = page.locator('#sim-symbols button');
    const switched = await buttons.count() > 1;
    if (switched) {
      await buttons.nth(1).click();
      await page.waitForTimeout(1000);
      if (!await buttons.nth(1).evaluate(e => e.classList.contains('primary'))) throw Error('Stock switch failed');
    }
    if (await page.locator('html').getAttribute('data-theme') === 'light') throw Error('Expected dark mode');
    await page.screenshot({ path: path.resolve('data/ui_check_sim.png'), fullPage: true });
    await request('/api/sim/control', { action: 'play' });
    let sold = false;
    for (let i = 0; i < 150; i++) {
      const st = await request('/api/sim');
      if (st.trades.some(t => t.day === state.day)) { sold = true; break; }
      await page.waitForTimeout(200);
    }
    await request('/api/sim/control', { action: 'pause' });
    if (!sold) throw Error('No visible sale');
    // A different selected stock may still be open when the first stock stops.
    const st = await request('/api/sim');
    const symbol = st.trades.find(t => t.day === state.day).symbol;
    await page.waitForTimeout(1000);
    await page.locator('#sim-symbols button').filter({ hasText: symbol }).click();
    await page.waitForSelector('#sim-candles circle');
    const quiet = state.days.find(d => !state.panic_days.includes(d.day)).day;
    await request('/api/sim/control', { action: 'day', value: quiet });
    await page.waitForFunction(() => document.querySelector('#sim-chart-note')?.textContent.startsWith('No trade today: NIFTY opened'));
    if (await page.locator('#sim-candles .level.entry').count()) throw Error('Stale entry on quiet day');
    if (!await page.locator('#sim-symbols').innerText().then(t => t.includes('NIFTY 50'))) throw Error('Missing NIFTY');
    if (errors.length) throw Error(errors.join('\n'));
    console.log(JSON.stringify({ week: state.week, day: state.day, clock: state.clock, entry_stop_buy: true, switch_checked: switched,
      sell_marker: true, no_trade_nifty: true, page_errors: errors, screenshot: 'data/ui_check_sim.png' }));
  } finally { await browser.close(); }
})().catch(err => { console.error(err); process.exitCode = 1; });
