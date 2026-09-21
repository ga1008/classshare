import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { describe, expect, it } from 'vitest';

// Exact private presentation functions from the page, no request/controller copy.
const source = readFileSync('static/js/profile.js', 'utf8');
function part(start: string, end: string) { return source.slice(source.indexOf(start), source.indexOf(end)); }
class Element {
  dataset: Record<string, string> = {}; attrs: Record<string, string> = {}; classes = new Set<string>();
  classList = { contains: (v: string) => this.classes.has(v), toggle: (v: string, on: boolean) => on ? this.classes.add(v) : this.classes.delete(v) };
  children: Element[] = []; parent: Element | null = null; disabled = false; className = ''; textContent = ''; tagName = 'SPAN'; value: string | number = ''; style: Record<string, string> = {};
  appendChild(node: Element) { this.children.push(node); node.parent = this; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
  setAttribute(key: string, value: string) { this.attrs[key] = value; }
  querySelector(_selector: string) { return this.children.find(node => 'profileBusySpinner' in node.dataset) || null; }
}
function functions(enabled = true, nodes: Record<string, Element[]> = {}) {
  const context: any = { profileLq: enabled, document: { createElement: () => new Element(), querySelectorAll: (key: string) => nodes[key] || [],
    querySelector: () => null, getElementById: () => null }, setText: () => {} };
  runInNewContext(part('function setButtonBusy(', 'function renderChart(') + part('function updateProfileChrome(', 'function initAvatarUpload(') + part('function applyMoodValue(', 'async function saveMood('), context);
  return context;
}
describe('LQ Profile presentation keeps native nodes and opt-in boundaries', () => {
  it('keeps the original LQ label node, name and space through repeat busy/settled changes', () => {
    const api = functions(); const button = new Element(); button.classes.add('lq-btn');
    const label = new Element(); label.textContent = '保存资料'; button.appendChild(label); button.textContent = '保存资料';
    api.setButtonBusy(button, true, '保存中'); api.setButtonBusy(button, true, '保存中');
    expect(button.children).toHaveLength(2); expect(button.children[0]).toBe(label); expect(label.textContent).toBe('保存资料');
    expect(button.attrs['aria-busy']).toBe('true'); expect(button.disabled).toBe(true);
    expect(button.children[1].attrs['aria-hidden']).toBe('true');
    api.setButtonBusy(button, false);
    expect(button.children).toEqual([label]); expect(button.disabled).toBe(false); expect(button.attrs['aria-busy']).toBe('false');
  });
  it('retains the original text-based busy behavior when the family is disabled', () => {
    const api = functions(false); const button = new Element(); button.classes.add('lq-btn'); button.textContent = '保存资料';
    api.setButtonBusy(button, true, '保存中'); expect(button.textContent).toBe('保存中'); expect(button.attrs['aria-busy']).toBeUndefined();
    api.setButtonBusy(button, false); expect(button.textContent).toBe('保存资料');
  });
  it('updates the same native progress value including zero while leaving old width behavior available', () => {
    const bar = new Element(); bar.tagName = 'PROGRESS'; bar.value = 90;
    functions(true, { '[data-profile-completion-bar]': [bar] }).updateProfileChrome({ completion: { percent: 0 } });
    expect(bar.value).toBe(0); expect(bar.style.width).toBeUndefined();
    const old = new Element(); functions(false, { '[data-profile-completion-bar]': [old] }).updateProfileChrome({ completion: { percent: 25 } });
    expect(old.style.width).toBe('25%');
  });
  it('synchronizes mood pressed state only for the new presentation', () => {
    const mood = new Element(); mood.dataset.profileMood = '专注';
    functions(true, { '[data-profile-mood]': [mood] }).applyMoodValue('专注'); expect(mood.attrs['aria-pressed']).toBe('true');
    functions(true, { '[data-profile-mood]': [mood] }).applyMoodValue('平静'); expect(mood.attrs['aria-pressed']).toBe('false');
    const old = new Element(); old.dataset.profileMood = '专注'; functions(false, { '[data-profile-mood]': [old] }).applyMoodValue('专注');
    expect(old.attrs['aria-pressed']).toBeUndefined(); expect(old.classes.has('is-active')).toBe(true);
  });
});
