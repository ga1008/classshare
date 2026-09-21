import { describe, expect, it } from 'vitest';
import { menuProps, menuMarkup } from '../../static/js/lq/menus.js';
import { tooltipProps, tooltipMarkup } from '../../static/js/lq/tooltips.js';
import fs from 'node:fs';
const fixtures = JSON.parse(fs.readFileSync(new URL('../e2e/components/fixtures/lq-menu-tooltip.json', import.meta.url), 'utf8'));
describe('LQ Menu/Tooltip pure contracts', () => {
    for (const props of fixtures.invalidMenus) it(`rejects invalid menu ${JSON.stringify(props)}`, () => expect(() => menuProps(props)).toThrow());
    for (const props of fixtures.invalidTooltips) it(`rejects invalid tooltip ${JSON.stringify(props)}`, () => expect(() => tooltipProps(props)).toThrow());
    it('uses one shared button contract with safe links, focusable disabled items and danger separation', () => {
        const value = menuMarkup(fixtures.menus[0]);
        expect(value).toContain('role="menuitem" tabindex="-1"'); expect(value).toContain('aria-disabled="true"');
        expect(value).not.toContain(' disabled='); expect(value).toContain('rel="noopener noreferrer"'); expect(value).toContain('role="separator"');
        expect(value).toContain('lq-btn--ghost'); expect(value).not.toContain('listbox');
    });
    it('only typed text reaches HTML in all entrances', () => {
        const menu = menuMarkup(fixtures.menus[1]), tip = tooltipMarkup(fixtures.tooltips[1]);
        expect(menu).not.toContain('<img'); expect(menu).toContain('&lt;img'); expect(tip).not.toContain('<img'); expect(tip).toContain('&lt;img');
    });
});
