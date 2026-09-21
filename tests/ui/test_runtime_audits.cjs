'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const { withPage } = require('../../tools/ui/audit_runtime.cjs');
const { inspectGlass } = require('../../tools/ui/audit_glass_layers.cjs');
const { inspectContrast, inspectTextPixels } = require('../../tools/ui/contrast_probe.cjs');

test('glass audit counts covered, pseudo, scrolling and modal layers without hidden ancestors', async () => {
  await withPage({ url: pathToFileURL(path.join(__dirname, 'fixtures/runtime-audit.html')).href }, async (page) => {
    const report = await inspectGlass(page);
    assert.equal(report.status, 'baseline');
    assert.equal(report.renderedLayerCount, 6);
    assert.equal(report.layers.find((layer) => layer.host === '#covered').centerOccluded, true);
    assert.equal(report.layers.filter((layer) => layer.host === '#pseudo').length, 2);
    assert.equal(report.layers.find((layer) => layer.host === '#scrolling').scrollingContainer, '#scroll');
    assert.equal(report.layers.some((layer) => layer.pseudo === '::backdrop'), true);
    assert.equal(report.layers.some((layer) => layer.host === '#transparent' || layer.host === '#hidden'), false);
    assert.equal((await inspectGlass(page, { budget: '5' })).status, 'failed');
    await page.locator('#dialog').evaluate((dialog) => dialog.close());
    assert.equal((await inspectGlass(page, { budget: '4' })).status, 'passed');
  });
});

test('contrast audit composites nested alpha, applies text thresholds and refuses unknown backgrounds', async () => {
  await withPage({ url: pathToFileURL(path.join(__dirname, 'fixtures/runtime-audit.html')).href }, async (page) => {
    await page.locator('#dialog').evaluate((dialog) => dialog.close());
    const report = await inspectContrast(page, { enforce: true });
    const sample = (host) => report.samples.find((item) => item.host === host);
    assert.equal(sample('#black').ratio, 21);
    assert.equal(sample('#black').status, 'passed');
    assert.ok(Math.abs(sample('#alpha').background[0] - 191.25) < 1);
    assert.ok(Math.abs(sample('#alpha').foreground[0] - 38.25) < 1);
    assert.equal(sample('#gradient').status, 'unmeasured');
    assert.equal(sample('#gradient').ratio, null);
    assert.equal(sample('#image').status, 'unmeasured');
    assert.equal(sample('#opacity').status, 'unmeasured');
    assert.equal(sample('#low').status, 'failed');
    assert.equal(sample('#large').threshold, 3);
    assert.equal(sample('#large').status, 'passed');
    assert.equal(report.status, 'failed');
    assert.equal(report.unmeasuredCount, 3);
  });
});

test('CLI without a URL explicitly reports unmeasured with exit code 2', () => {
  for (const name of ['audit_glass_layers', 'contrast_probe']) {
    const result = spawnSync(process.execPath, [path.join(__dirname, `../../tools/ui/${name}.cjs`)], { encoding: 'utf8' });
    assert.equal(result.status, 2);
    assert.equal(JSON.parse(result.stdout).status, 'unmeasured');
  }
});

test('pixel audit measures real gradients and preserves text paint after capture', async () => {
  await withPage({ url: pathToFileURL(path.join(__dirname, 'fixtures/pixel-contrast.html')).href }, async page => {
    const report = await inspectTextPixels(page);
    const byText = text => report.samples.find(sample => sample.text === text);
    assert.equal(byText('Black on white').ratio, 21);
    assert.equal(byText('White on white').ratio, 1);
    assert.equal(byText('White on gradient').status, 'failed');
    assert.ok(byText('White on gradient').worst.background[0] > 230);
    assert.ok(byText('Black on white').sampledPixels > 100);
    assert.equal(await page.locator('#black').evaluate(node => getComputedStyle(node).color), 'rgb(0, 0, 0)');
    assert.equal(await page.locator('#white').evaluate(node => getComputedStyle(node).color), 'rgb(255, 255, 255)');
    assert.equal(report.status, 'failed');
  });
});

test('pixel audit refuses unsupported foreground compositing and missing samples', async () => {
  await withPage({ url: pathToFileURL(path.join(__dirname, 'fixtures/pixel-contrast.html')).href }, async page => {
    await assert.rejects(inspectTextPixels(page, { selector: '.absent' }), /No marked/);
    await page.locator('#black').evaluate(node => node.parentElement.style.opacity = '.5');
    await assert.rejects(inspectTextPixels(page), /Group opacity/);
  });
});
