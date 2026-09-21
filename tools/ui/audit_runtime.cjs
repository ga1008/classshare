'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('@playwright/test');

function readOptions(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith('--')) throw new Error(`Unexpected argument: ${arg}`);
    const key = arg.slice(2);
    if (['enforce', 'help'].includes(key)) options[key] = true;
    else {
      if (!argv[i + 1] || argv[i + 1].startsWith('--')) throw new Error(`Missing value for ${arg}`);
      options[key] = argv[++i];
    }
  }
  return options;
}

function writeReport(report, output) {
  const json = `${JSON.stringify(report, null, 2)}\n`;
  if (output) {
    fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true });
    fs.writeFileSync(output, json, 'utf8');
  }
  process.stdout.write(json);
}

async function withPage(options, inspect) {
  const url = new URL(options.url);
  if (!['http:', 'https:', 'file:'].includes(url.protocol)) throw new Error('Use an http(s) or local file URL.');
  const match = /^(\d+)x(\d+)$/.exec(options.viewport || '1440x980');
  if (!match || Number(match[1]) < 1 || Number(match[2]) < 1) throw new Error('--viewport must be WIDTHxHEIGHT.');
  const browserPath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  const browser = await chromium.launch({
    headless: true,
    ...(options.channel ? { channel: options.channel } : fs.existsSync(browserPath) ? { executablePath: browserPath } : {}),
  });
  try {
    const context = await browser.newContext({
      viewport: { width: Number(match[1]), height: Number(match[2]) },
      ...(options['storage-state'] ? { storageState: options['storage-state'] } : {}),
    });
    const page = await context.newPage();
    const response = await page.goto(url.href, { waitUntil: 'load' });
    if (response && !response.ok()) throw new Error(`Page returned HTTP ${response.status()}`);
    if (options['wait-for']) await page.locator(options['wait-for']).waitFor({ state: 'visible' });
    await page.evaluate(() => document.fonts.ready);
    const animationStability = await page.evaluate(async () => {
      const finiteAnimations = () => document.getAnimations().filter((animation) => animation.playState === 'running'
        && Number.isFinite(animation.effect?.getComputedTiming().endTime));
      const pending = finiteAnimations();
      // Wait for actual entrance animation completion, with a bounded escape
      // for a page that continually adds animations. Infinite ambient motion
      // remains a sampled instant and needs a separate performance trace.
      await Promise.race([
        Promise.all(pending.map((animation) => animation.finished.catch(() => undefined))),
        new Promise((resolve) => setTimeout(resolve, 2000)),
      ]);
      return { awaitedFiniteAnimations: pending.length, unfinishedFiniteAnimations: finiteAnimations().length };
    });
    // A screenshot can support manual review of unmeasured image/gradient text.
    if (options.screenshot) {
      fs.mkdirSync(path.dirname(path.resolve(options.screenshot)), { recursive: true });
      await page.screenshot({ path: options.screenshot, fullPage: true });
    }
    return { ...await inspect(page), finalUrl: page.url(), navigationRedirected: !!response?.request().redirectedFrom(), animationStability };
  } finally {
    await browser.close();
  }
}

async function runCli(kind, inspect, argv = process.argv.slice(2)) {
  let options = {};
  try {
    options = readOptions(argv);
    if (options.help) {
      process.stdout.write(`${kind}: --url URL [--output report.json] [--viewport 390x844] [--storage-state file] [--wait-for selector] [--screenshot file] [--channel chrome] [--budget N] [--enforce]\n`);
      return;
    }
    if (!options.url) {
      writeReport({ tool: kind, status: 'unmeasured', reason: 'No --url supplied; no browser measurement was performed.' }, options.output);
      process.exitCode = 2;
      return;
    }
    const result = await withPage(options, (page) => inspect(page, options));
    writeReport({ tool: kind, url: options.url, measuredAt: new Date().toISOString(), ...result }, options.output);
    if (result.enforced && result.status !== 'passed') process.exitCode = result.status === 'failed' ? 1 : 2;
  } catch (error) {
    writeReport({ tool: kind, status: 'unmeasured', reason: error.message }, options.output);
    process.exitCode = 2;
  }
}

module.exports = { readOptions, withPage, runCli };
