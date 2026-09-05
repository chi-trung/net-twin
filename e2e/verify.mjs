/**
 * Chrome DevTools E2E harness for net-twin.
 *
 * Drives the system Chrome via CDP (puppeteer-core, no downloaded browser)
 * against the running Docker stack at http://localhost:5173.
 *
 * Usage:
 *   node verify.mjs          # full suite, screenshots into shots/
 */
import { mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const CHROME_CANDIDATES = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  `${process.env.LOCALAPPDATA}/Google/Chrome/Application/chrome.exe`,
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
];
const BASE = process.env.E2E_BASE ?? 'http://localhost:5173';
const ROOT = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(ROOT, 'shots');

const results = [];
let browser;

function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? '  ✔' : '  ✘'} ${name}${detail ? ` — ${detail}` : ''}`);
}

async function openPage() {
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`));
  page.on('requestfailed', (req) => {
    const failure = req.failure()?.errorText ?? '';
    // ignore aborted (navigation races) and WS connections closing
    if (!failure.includes('ERR_ABORTED')) {
      consoleErrors.push(`requestfailed: ${req.url()} ${failure}`);
    }
  });
  page._consoleErrors = consoleErrors;
  await page.setViewport({ width: 1600, height: 1000 });
  return page;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Tap a cytoscape element through a real mouse click.
 *
 * Cytoscape draws to a canvas, so we resolve the element's rendered position
 * from the exposed window.cy instance. Live twin events periodically refetch
 * the topology and re-run the (worker-based) fcose layout, which can move
 * elements between the position read and the click — so we retry: re-read
 * fresh coordinates on every attempt until the expected panel appears.
 */
async function tapElement(page, findEle, arg, waitFor, { attempts = 8, settle = 1200 } = {}) {
  for (let i = 0; i < attempts; i++) {
    const pos = await page.evaluate(findEle, arg);
    if (!pos) return 'element-not-found';
    await page.mouse.click(pos.x, pos.y);
    try {
      await page.waitForSelector(waitFor, { timeout: settle });
      return 'ok';
    } catch {
      /* layout churn may have moved it — retry with fresh coordinates */
    }
  }
  return 'no-tap-after-retries';
}

async function topologyCounts(page) {
  // nodes/links live in the twinStore (zustand-style external store), not in DOM
  return page.evaluate(() => {
    const stats = document.querySelector('.topbar-stats')?.textContent ?? '';
    const nodes = /(\d+)\s+nodes/.exec(stats)?.[1];
    const links = /(\d+)\s+links/.exec(stats)?.[1];
    return { nodes: nodes ? Number(nodes) : 0, links: links ? Number(links) : 0 };
  });
}

async function main() {
  await mkdir(SHOTS, { recursive: true });
  const executablePath = CHROME_CANDIDATES.find((p) => existsSync(p));
  if (!executablePath) throw new Error('no Chrome/Edge found');
  console.log(`chrome: ${executablePath}\nbase:   ${BASE}\n`);

  browser = await puppeteer.launch({
    executablePath,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });

  try {
    await testLoadAndRealtime();
    await testWhatIf();
    await testPathTrace();
    await testLinkTraffic();
    await testPdfButton();
    await testTimeTravel();
  } finally {
    await browser.close();
  }

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) {
    console.log('FAILED:');
    for (const f of failed) console.log(`  ✘ ${f.name} — ${f.detail}`);
    process.exit(1);
  }
}

/* ── 1. page loads, WS goes live, KPI row + topology render ─────────── */
async function testLoadAndRealtime() {
  console.log('\n[1] load / realtime / KPIs / topology');
  const page = await openPage();
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 30000 });

  check('app mounted', (await page.$('.app')) !== null);
  check('no console errors on load', page._consoleErrors.length === 0,
    page._consoleErrors.slice(0, 3).join(' | '));

  const badge = await page.$eval('.conn-badge', (el) => ({ cls: el.className, txt: el.textContent }));
  check('websocket live', badge.cls.includes('conn-open'), `badge="${badge.txt}"`);

  // topology builds over the first cycles; wait up to 30s
  let counts = { nodes: 0, links: 0 };
  for (let i = 0; i < 30; i++) {
    counts = await topologyCounts(page);
    if (counts.nodes >= 15) break;
    await sleep(1000);
  }
  check('>= 15 nodes discovered', counts.nodes >= 15, `${counts.nodes} nodes`);
  check('>= 14 links inferred', counts.links >= 14, `${counts.links} links`);

  // KPI row populated by /overview
  await page.waitForSelector('.stat-tile', { timeout: 15000 });
  const tiles = await page.$$eval('.stat-tile .st-label', (els) => els.map((e) => e.textContent));
  check('KPI row has 6 tiles', tiles.length === 6, tiles.join(', '));
  const kpi = await page.evaluate(() => {
    const get = (label) =>
      [...document.querySelectorAll('.stat-tile')].find((t) =>
        t.querySelector('.st-label')?.textContent?.toLowerCase().includes(label)
      )?.querySelector('.st-value')?.textContent;
    return { devices: get('device'), up: get('reachable'), alerts: get('alert') };
  });
  check('KPI devices == 15', kpi.devices === '15', `got ${kpi.devices}`);
  check('KPI reachable == 15', kpi.up === '15', `got ${kpi.up}`);
  check('KPI active alerts numeric', /^\d+$/.test(kpi.alerts ?? ''), `got ${kpi.alerts}`);

  // health-mix bar with legend (legend lists only *present* health states)
  check('health-mix bar rendered', (await page.$('.health-mix .hm-seg')) !== null);
  const legend = await page.$$eval('.hm-key', (els) => els.map((e) => e.textContent?.trim()));
  check('legend rendered for present states', legend.length >= 1 && legend.some((t) => t?.startsWith('up')),
    legend.join(', '));

  // device table on the right
  const rows = await page.$$eval('.device-table tbody tr', (els) => els.length);
  check('device table rows == 15', rows === 15, `${rows} rows`);

  await page.screenshot({ path: path.join(SHOTS, '01-overview.png') });
  await page.close();
}

/* ── 2. what-if: select node → blast radius report ──────────────────── */
async function testWhatIf() {
  console.log('\n[2] what-if blast radius');
  const page = await openPage();
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction(
    () => /(\d+)\s+nodes/.exec(document.querySelector('.topbar-stats')?.textContent ?? '')?.[1] >= 15,
    { timeout: 45000 }
  );

  await page.$$eval('.graph-toolbar .tool-btn', (btns) =>
    btns.find((b) => b.textContent?.includes('What-if'))?.click()
  );
  await sleep(300);
  const hint = await page.$eval('.tool-hint', (el) => el.textContent);
  check('what-if mode active', hint && hint.length > 3, `hint="${hint}"`);

  // fetch a mid-tier switch from the API, then click its row in the table
  const target = await page.evaluate(async () => {
    const r = await fetch('/api/v1/devices');
    const devices = await r.json();
    return devices.find((d) => d.name === 'dist-sw-01') ?? devices.find((d) => d.device_type === 'switch');
  });
  check('target device found (dist-sw-01)', Boolean(target?.id), target?.name ?? 'none');

  // ── real UI click path: cytoscape draws to canvas, so resolve the node's
  // rendered position from the cy instance (exposed as window.cy) and tap it.
  // The retry loop defeats layout churn from live twin events.
  const tapped = await page.evaluate((name) => {
    const cy = window.cy;
    if (!cy) return null;
    const node = cy.nodes().filter((n) => n.data('label') === name)[0];
    if (!node) return null;
    cy.animate({ center: { eles: node }, zoom: 1.2 }, { duration: 0 });
    const pos = node.renderedPosition();
    const rect = cy.container().getBoundingClientRect();
    return { x: rect.left + pos.x, y: rect.top + pos.y };
  }, 'dist-sw-01');
  check('cy instance exposed + node located', Boolean(tapped), tapped ? `${Math.round(tapped.x)},${Math.round(tapped.y)}` : 'window.cy missing');

  const tap = await tapElement(
    page,
    (name) => {
      const cy = window.cy;
      const node = cy?.nodes().filter((n) => n.data('label') === name)[0];
      if (!node) return null;
      const pos = node.renderedPosition();
      const rect = cy.container().getBoundingClientRect();
      return { x: rect.left + pos.x, y: rect.top + pos.y };
    },
    'dist-sw-01',
    '.whatif-report',
    { attempts: 8, settle: 1500 },
  );
  check('node tap opens what-if report', tap === 'ok', tap);
  const overlayText = await page.$eval('.whatif-report', (el) => el.textContent ?? '');
  const overlayOk = overlayText.toLowerCase().includes('isolated') || overlayText.includes('Impacted') || overlayText.includes('impacted');
  check('report shows blast-radius stats', overlayOk, overlayText.slice(0, 90));
  if (overlayOk) {
    const isoCount = await page.$$eval('.whatif-report .wi-dev', (els) => els.length);
    check('report lists affected devices', isoCount > 0, `${isoCount} chips`);
    const btn = await page.$('.whatif-actions .tool-btn');
    check('apply-outage button present', Boolean(btn), btn ? 'yes' : 'no');
  }
  await page.screenshot({ path: path.join(SHOTS, '02-whatif.png') });
  await page.close();
}

/* ── 3. path trace between core and a host ──────────────────────────── */
async function testPathTrace() {
  console.log('\n[3] path tracing');
  const page = await openPage();
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction(
    () => /(\d+)\s+nodes/.exec(document.querySelector('.topbar-stats')?.textContent ?? '')?.[1] >= 15,
    { timeout: 45000 }
  );

  const ids = await page.evaluate(async () => {
    const devices = await (await fetch('/api/v1/devices')).json();
    const core = devices.find((d) => d.device_type === 'router');
    const host = devices.find((d) => d.name.includes('host-018'));
    return { core: core?.id, host: host?.id };
  });
  const path = await page.evaluate(async ({ core, host }) => {
    const r = await fetch(`/api/v1/topology/path?from=${core}&to=${host}`);
    return r.json();
  }, ids);
  check('path found core→host-018', path.found === true, `${path.hops} hops`);
  check('path has 4 devices (3 hops)', path.device_ids?.length === 4,
    (path.device_ids ?? []).length + ' ids');
  check('link ids match hops', (path.link_ids?.length ?? 0) === path.hops,
    `${(path.link_ids ?? []).length} links`);
  await page.close();
}

/* ── 4. link traffic: click an edge → chart renders ─────────────────── */
async function testLinkTraffic() {
  console.log('\n[4] link traffic analytics');
  const page = await openPage();
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction(
    () => /(\d+)\s+links/.exec(document.querySelector('.topbar-stats')?.textContent ?? '')?.[1] >= 14,
    { timeout: 45000 }
  );

  const link = await page.evaluate(async () => {
    const topo = await (await fetch('/api/v1/topology')).json();
    return topo.edges[0];
  });
  const traffic = await page.evaluate(async (id) => {
    const r = await fetch(`/api/v1/links/${id}/metrics`);
    return r.json();
  }, link.id);
  check('link metrics endpoint returns points', traffic.points?.length > 0,
    `${traffic.points?.length ?? 0} points`);

  // UI: tap the heaviest edge at its rendered midpoint via window.cy
  // (same retry strategy as the node tap — layout churn moves edges too)
  // cy instance is created when the topology query resolves — wait for edges
  await page.waitForFunction(() => window.cy && window.cy.edges().length > 0, { timeout: 30000 });
  const edgeTap = await page.evaluate(() => {
    const cy = window.cy;
    if (!cy) return null;
    const edge = cy.edges()[0];
    if (!edge) return null;
    cy.animate({ center: { eles: edge }, zoom: 1.2 }, { duration: 0 });
    const p1 = edge.source().renderedPosition();
    const p2 = edge.target().renderedPosition();
    const rect = cy.container().getBoundingClientRect();
    return { x: rect.left + (p1.x + p2.x) / 2, y: rect.top + (p1.y + p2.y) / 2 };
  });
  check('edge midpoint located', Boolean(edgeTap), edgeTap ? `${Math.round(edgeTap.x)},${Math.round(edgeTap.y)}` : 'no edge');
  const edgeTapResult = await tapElement(
    page,
    () => {
      const cy = window.cy;
      const edge = cy?.edges()[0];
      if (!edge) return null;
      // renderedMidpoint follows the bezier curve; the straight midpoint
      // between endpoints can fall off the rendered line
      const mid = edge.renderedMidpoint?.() ?? {
        x: (edge.source().renderedPosition().x + edge.target().renderedPosition().x) / 2,
        y: (edge.source().renderedPosition().y + edge.target().renderedPosition().y) / 2,
      };
      const rect = cy.container().getBoundingClientRect();
      return { x: rect.left + mid.x, y: rect.top + mid.y };
    },
    undefined,
    '.panel-side .link-facts, .panel-side .detail-panel, .panel-side canvas',
    { attempts: 6, settle: 1500 },
  );
  check('edge tap registered', edgeTapResult === 'ok', edgeTapResult);
  const linkPanel = await page.evaluate(() => document.querySelector('.panel-side')?.textContent ?? '');
  const hasTrafficChart = linkPanel.includes('Mbps') || linkPanel.includes('Kbps') || linkPanel.includes('bps');
  check('edge tap opens link/traffic panel', hasTrafficChart,
    hasTrafficChart ? linkPanel.match(/[\d.]+\s*[KM]?bps/)?.[0] ?? 'bps visible' : 'not found');
  // ECharts canvas actually painted inside the side panel
  const chartCanvas = await page.$$eval('.panel-side canvas', (els) => els.length);
  check('traffic chart canvas painted', chartCanvas > 0, `${chartCanvas} canvas in side panel`);
  await page.screenshot({ path: path.join(SHOTS, '03-link.png') });
  await page.close();
}

/* ── 5. PDF report button downloads a valid PDF ─────────────────────── */
async function testPdfButton() {
  console.log('\n[5] PDF report export');
  const page = await openPage();
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 30000 });

  // fetch through the same origin the button uses
  const resp = await page.evaluate(async () => {
    const r = await fetch('/api/v1/reports/health.pdf');
    const buf = await r.arrayBuffer();
    const bytes = new Uint8Array(buf.slice(0, 5));
    return {
      status: r.status,
      type: r.headers.get('content-type'),
      disposition: r.headers.get('content-disposition') ?? '',
      magic: String.fromCharCode(...bytes),
      size: buf.byteLength,
    };
  });
  check('PDF endpoint 200', resp.status === 200, `status ${resp.status}`);
  check('content-type application/pdf', resp.type === 'application/pdf', resp.type);
  check('attachment disposition', resp.disposition.includes('attachment'), resp.disposition.slice(0, 60));
  check('PDF magic %PDF-', resp.magic === '%PDF-', resp.magic);
  check('PDF > 1KB', resp.size > 1000, `${resp.size} bytes`);
  check('button href correct', (await page.$eval('.export-btn', (el) => el.getAttribute('href'))) === '/api/v1/reports/health.pdf');
  await page.close();
}

/* ── 6. time travel: snapshot timeline, pick, diff, back to live ────── */
async function testTimeTravel() {
  console.log('\n[6] time travel / snapshot timeline');
  const page = await openPage();
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 30000 });
  await page.waitForFunction(
    () => /(\d+)\s+nodes/.exec(document.querySelector('.topbar-stats')?.textContent ?? '')?.[1] >= 15,
    { timeout: 45000 }
  );

  // capture a fresh snapshot so the timeline is never empty
  const captured = await page.evaluate(async () => {
    const r = await fetch('/api/v1/snapshots', { method: 'POST' });
    return r.json();
  });
  check('manual capture works', Boolean(captured?.id), `snapshot ${captured?.id}, ${captured?.node_count}n`);

  // enter time-travel mode via the real toolbar button
  await page.$$eval('.graph-toolbar .tool-btn', (btns) =>
    btns.find((b) => b.textContent?.includes('Time travel'))?.click()
  );
  await page.waitForSelector('.time-travel', { timeout: 10000 });
  check('time-travel panel opens', true);

  const rows = await page.$$eval('.tt-point', (els) => els.length);
  check('timeline lists snapshots', rows >= 1, `${rows} points`);

  // pick the first (newest) snapshot → its graph renders on the canvas
  await page.click('.tt-point');
  await page.waitForFunction(
    () => window.cy && window.cy.nodes().length > 0,
    { timeout: 15000 }
  );
  const graphInfo = await page.evaluate(() => ({
    nodes: window.cy.nodes().length,
    edges: window.cy.edges().length,
  }));
  check('historical graph rendered on canvas', graphInfo.nodes >= 15,
    `${graphInfo.nodes} nodes · ${graphInfo.edges} edges`);

  // diff report for the picked snapshot appears inline
  const diffShown = await page.waitForSelector('.tt-diff', { timeout: 10000 })
    .then(() => true).catch(() => false);
  check('diff report rendered', diffShown);

  // back to live restores the interactive live view
  await page.$$eval('.time-travel .tool-btn', (btns) =>
    btns.find((b) => b.textContent?.includes('Back to live'))?.click()
  );
  await page.waitForFunction(() => !document.querySelector('.time-travel'), { timeout: 5000 });
  check('back to live closes panel', true);
  await page.screenshot({ path: path.join(SHOTS, '04-timetravel.png') });
  await page.close();
}

main().catch(async (err) => {
  console.error('E2E crashed:', err.message);
  await browser?.close().catch(() => {});
  process.exit(1);
});
