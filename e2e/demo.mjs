/**
 * Guided UI walkthrough — drives the real app through its four headline
 * flows and saves a screenshot of each step into shots/demo/.
 *
 *   node demo.mjs
 */
import { mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import puppeteer from 'puppeteer-core';

const CHROME = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  `${process.env.LOCALAPPDATA}/Google/Chrome/Application/chrome.exe`,
].find((p) => existsSync(p));
const BASE = process.env.E2E_BASE ?? 'http://localhost:5173';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Click a toolbar button by its visible text. */
async function clickTool(page, text) {
  await page.$$eval('.graph-toolbar .tool-btn', (btns, t) => {
    btns.find((b) => b.textContent?.includes(t))?.click();
  }, text);
  await sleep(300);
}

/** Real mouse click on a cytoscape node (canvas — no DOM), with retries. */
async function tapNode(page, label, waitFor, { attempts = 8, settle = 1500 } = {}) {
  for (let i = 0; i < attempts; i++) {
    const pos = await page.evaluate((name) => {
      const cy = window.cy;
      const node = cy?.nodes().filter((n) => n.data('label') === name)[0];
      if (!node) return null;
      const p = node.renderedPosition();
      const r = cy.container().getBoundingClientRect();
      return { x: r.left + p.x, y: r.top + p.y };
    }, label);
    if (!pos) throw new Error(`node "${label}" not found`);
    await page.mouse.click(pos.x, pos.y);
    try {
      await page.waitForSelector(waitFor, { timeout: settle });
      return;
    } catch {
      /* layout churn may have moved the node — retry */
    }
  }
  throw new Error(`tap on "${label}" never produced "${waitFor}"`);
}

async function main() {
  const shots = new URL('shots/demo/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
  await mkdir(shots, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1600, height: 1000 });
    await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 30000 });

    // wait for a fully discovered topology before starting
    await page.waitForFunction(
      () => {
        const s = document.querySelector('.topbar-stats')?.textContent ?? '';
        return /15 nodes/.test(s) && /14 links/.test(s);
      },
      { timeout: 60000 },
    );
    await sleep(1500);
    await page.screenshot({ path: `${shots}1-overview.png` });
    console.log('✔ 1-overview.png — dashboard + live twin topology');

    // ── what-if: simulate dist-sw-01 failure ──────────────────────
    await clickTool(page, 'What-if');
    await tapNode(page, 'dist-sw-01', '.whatif-report');
    await sleep(600);
    await page.screenshot({ path: `${shots}2-whatif.png` });
    console.log('✔ 2-whatif.png — blast-radius report + graph highlight');

    // ── path: trace core → host-018 (mode switch resets state) ────
    await clickTool(page, 'Path');
    await tapNode(page, 'core-rtr-01', '.tool-hint');
    await tapNode(page, 'host-018', '.whatif-report');
    await sleep(600);
    await page.screenshot({ path: `${shots}3-path.png` });
    console.log('✔ 3-path.png — shortest-path trace core → host-018');

    // ── explore: tap a link for its live traffic chart ────────────
    await clickTool(page, 'Explore');
    for (let i = 0; i < 8; i++) {
      const pos = await page.evaluate(() => {
        const edge = window.cy?.edges()[0];
        if (!edge) return null;
        const mid = edge.renderedMidpoint?.() ?? {
          x: (edge.source().renderedPosition().x + edge.target().renderedPosition().x) / 2,
          y: (edge.source().renderedPosition().y + edge.target().renderedPosition().y) / 2,
        };
        const r = window.cy.container().getBoundingClientRect();
        return { x: r.left + mid.x, y: r.top + mid.y };
      });
      if (!pos) throw new Error('no edges in graph');
      await page.mouse.click(pos.x, pos.y);
      try {
        await page.waitForSelector('.panel-side .link-facts', { timeout: 1500 });
        break;
      } catch { /* retry */ }
    }
    await sleep(1500); // let ECharts paint
    await page.screenshot({ path: `${shots}4-link-traffic.png` });
    console.log('✔ 4-link-traffic.png — link detail + live throughput chart');

    await browser.close();
    console.log(`\ndone — screenshots in e2e/shots/demo/`);
  } catch (err) {
    await browser?.close().catch(() => {});
    console.error('demo failed:', err.message);
    process.exit(1);
  }
}

main();
