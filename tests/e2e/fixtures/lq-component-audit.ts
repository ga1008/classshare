import type { Page } from '@playwright/test';

/** Test-only DOM inventory. It never mutates controls or claims acceptance. */
type SourceEntry = { file: string; line: number; kind: string; id: string; class: string; suggestedOwner: string; auditId: string };
export async function collectComponentDom(page: Page, sourceEntries: SourceEntry[] = []) {
  const frames = [];
  for (const frame of page.frames()) {
    try {
      const frameUrl = new URL(frame.url() || 'about:blank');
      const documentContent = frame !== page.mainFrame() && /^\/materials\/(?:render\/\d+\/|lessondoc-editor\/\d+\/preview(?:\/|$))/.test(frameUrl.pathname);
      const scope = documentContent ? 'document-content' : 'platform-ui';
      const result = await frame.evaluate(() => {
        const normalize: Record<string, string> = { input: 'field', textarea: 'field', file: 'field', range: 'field', color: 'field', checkbox: 'choice', radio: 'choice', switch: 'choice', disclosure: 'button', workspace: 'surface', drawer: 'dialog', popover: 'menu', selection: 'select' };
        Object.assign(normalize, { region: 'domain', card: 'surface', list: 'surface', row: 'surface', empty: 'surface', prose: 'surface', bubble: 'surface', page_head: 'toolbar', filter_bar: 'toolbar', form_section: 'field', error_summary: 'field', form_actions: 'toolbar', badge: 'status', avatar: 'status', spinner: 'status', progress: 'status', skeleton: 'status', status: 'status', save_status: 'status', alert: 'status', conflict: 'status', topbar: 'toolbar', nav_item: 'button', sidebar: 'toolbar', dock: 'toolbar', fab: 'button', crumbs: 'toolbar', steps: 'toolbar', editor: 'toolbar', page_layout: 'toolbar', viewer: 'surface', split: 'surface', tabs: 'tab', segment: 'tab' });
        const kinds = new Set(['status', 'button', 'field', 'select', 'surface', 'menu', 'dialog', 'tab', 'choice', 'toolbar', 'layer', 'content-slot', 'handle', 'chip', 'domain']);
        const canonical: Record<string, string[]> = {
          button: ['lq-btn', 'lq-disclosure-trigger'], field: ['lq-form-section', 'lq-field', 'lq-input', 'lq-textarea', 'lq-range', 'lq-native-file', 'lq-native-color'],
          input: ['lq-input'], textarea: ['lq-textarea'], file: ['lq-native-file'], color: ['lq-native-color'], range: ['lq-range'],
          select: ['lq-select', 'lq-selection'], choice: ['lq-domain-graph-choice', 'lq-btn', 'lq-checkbox', 'lq-radio', 'lq-switch', 'lq-chip'], checkbox: ['lq-checkbox'], radio: ['lq-radio'], switch: ['lq-switch'],
          disclosure: ['lq-disclosure-trigger'], surface: ['lq-surface', 'lq-card', 'lq-glass'], dialog: ['lq-native-dialog', 'lq-glass', 'lq-dialog', 'lq-dialog-root', 'lq-sheet', 'lq-drawer', 'lq-domain-popover'],
          menu: ['lq-menu__item', 'lq-menu', 'lq-nav-menu', 'lq-popover', 'lq-domain-region', 'lq-domain-popover'], selection: ['lq-selection', 'lq-domain-raised'], popover: ['lq-glass', 'lq-popover', 'lq-domain-popover'],
          tab: ['lq-tabs', 'lq-tabs__tab', 'lq-tabs__list', 'lq-segment'], workspace: ['lq-workspace', 'lq-domain-workspace'], drawer: ['lq-drawer', 'lq-glass'], toolbar: ['lq-domain-toolbar', 'lq-toolbar', 'lq-topbar', 'lq-filter-bar', 'lq-page-head'],
          layer: ['lq-domain-region', 'lq-layer'], 'content-slot': ['lq-domain-content-slot'], handle: ['lq-domain-handle'], chip: ['lq-chip'], domain: ['lq-domain-region', 'lq-domain-control'], region: ['lq-domain-region'],
        };
        for (const kind of ['badge', 'avatar', 'spinner', 'progress', 'skeleton', 'status', 'save_status', 'alert', 'conflict', 'card', 'list', 'row', 'empty', 'page_head', 'filter_bar', 'prose', 'bubble', 'form_section', 'form_actions', 'error_summary', 'topbar', 'nav_item', 'sidebar', 'dock', 'fab', 'crumbs', 'steps', 'editor', 'page_layout', 'viewer', 'split', 'tabs', 'segment']) canonical[kind] = ['lq-' + kind.replaceAll('_', '-')];
        const classify = (el: Element): string | null => {
          const declared = el.getAttribute('data-lq-component');
          if (declared) return kinds.has(declared) ? declared : normalize[declared] || 'unknown';
          const tag = el.tagName.toLowerCase(), role = el.getAttribute('role'), type = el.getAttribute('type');
          if (tag === 'dialog' || ['dialog', 'alertdialog'].includes(role || '')) return 'dialog';
          if (['tab', 'tablist'].includes(role || '')) return 'tab';
          if (['menu', 'menubar', 'menuitem'].includes(role || '')) return 'menu';
          if (tag === 'select' || ['combobox', 'listbox'].includes(role || '')) return 'select';
          if (tag === 'input' && ['checkbox', 'radio'].includes(type || '') || ['checkbox', 'radio', 'switch'].includes(role || '')) return 'choice';
          if (['button', 'summary'].includes(tag) || role === 'button' || tag === 'input' && ['button', 'submit', 'reset'].includes(type || '')) return 'button';
          if (tag === 'input' && type === 'hidden') return null;
          if (['input', 'textarea', 'fieldset'].includes(tag)) return 'field';
          if (role === 'toolbar') return 'toolbar';
          const cls = el.getAttribute('class') || '';
          if (['label', 'span', 'strong', 'em', 'small', 'h1', 'h2', 'h3', 'p', 'i'].includes(tag)) return null;
          if (/(?:^|\s)[\w-]*(?:card|panel|surface)(?:\s|$)/.test(cls)) return 'surface';
          if (/(?:^|\s)[\w-]*(?:modal|dialog|sheet|drawer|overlay)(?:\s|$)/.test(cls)) return 'dialog';
          if (/(?:^|\s)[\w-]*(?:toolbar|topbar|filter-bar)(?:\s|$)/.test(cls)) return 'toolbar';
          if (/(?:^|\s)[\w-]*(?:menu|dropdown|popover)(?:\s|$)/.test(cls)) return 'menu';
          if (tag === 'a' && /(?:^|\s)(?:btn|lq-btn|[\w-]*-button)(?:\s|$)/.test(cls)) return 'button';
          return null;
        };
        const nodes: Element[] = [];
        const visit = (root: Document | ShadowRoot) => {
          for (const node of root.querySelectorAll('*')) { nodes.push(node); if (node.shadowRoot) visit(node.shadowRoot); }
        };
        visit(document);
        const blurHosts: { index: number; tag: string; id: string; class: string; pseudo: string | null; filter: string; leaf: boolean }[] = [];
        const overflowCandidates: { index: number; tag: string; id: string; class: string; left: number; right: number; width: number; overflowX: string; minWidth: string }[] = [];
        const styles = new Map<Element, CSSStyleDeclaration>();
        for (const [index, el] of nodes.entries()) {
          const style = getComputedStyle(el); styles.set(el, style); const rect = el.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0' || el.closest('[hidden]')) continue;
          if (rect.right > innerWidth + 1 || rect.left < -1) overflowCandidates.push({ index, tag: el.tagName.toLowerCase(), id: el.id, class: (el.getAttribute('class') || '').slice(0, 300), left: rect.left, right: rect.right, width: rect.width, overflowX: style.overflowX, minWidth: style.minWidth });
          for (const pseudo of [null, '::before', '::after']) {
            const candidate = pseudo ? getComputedStyle(el, pseudo) : style;
            if (pseudo && (!candidate.content || ['none', 'normal'].includes(candidate.content) || candidate.display === 'none' || candidate.visibility === 'hidden')) continue;
            const filter = candidate.backdropFilter || candidate.getPropertyValue('-webkit-backdrop-filter');
            if (filter && filter !== 'none') {
              const nativeLeaf = el.closest('button,input:not([type="hidden"]),select,textarea,[role="button"],[role="tab"],[role="checkbox"],[role="radio"],[role="switch"],[role="slider"],[role="combobox"],[role="menuitem"]');
              blurHosts.push({ index, tag: el.tagName.toLowerCase(), id: el.id, class: (el.getAttribute('class') || '').slice(0, 300), pseudo, filter, leaf: Boolean(nativeLeaf) || ['button', 'field', 'select', 'choice', 'tab', 'chip', 'handle'].includes(classify(el) || '') });
            }
          }
        }
        const records = nodes.flatMap((el, index) => {
          const kind = classify(el); if (!kind) return [];
          const style = styles.get(el)!, rect = el.getBoundingClientRect();
          const rendered = rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          const declared = el.getAttribute('data-lq-component');
          const className = el.getAttribute('class') || '';
          const explicitOwner = el.getAttribute('data-lq-owner') || el.closest('[data-lq-owner]')?.getAttribute('data-lq-owner') || null;
          const lqMarked = /(?:^|\s)lq-/.test(className);
          const expected = [...(canonical[declared || kind] || [])];
          if (['dialog', 'popover', 'drawer', 'selection', 'surface'].includes(declared || kind) && el.getAttribute('data-lq-material') === 'raised') expected.push('lq-domain-raised');
          const canonicalClasses = expected.filter(cls => el.classList.contains(cls) && (cls !== 'lq-domain-graph-choice' || el.namespaceURI === 'http://www.w3.org/2000/svg' && el.tagName.toLowerCase() === 'g' && el.getAttribute('role') === 'button' && el.getAttribute('tabindex') === '0'));
          const panel = kind === 'layer' ? el.querySelector('.lq-surface, .lq-dialog, .lq-sheet, .lq-drawer, .lq-domain-popover, .lq-workspace, .lq-domain-workspace, [data-lq-material="raised"].lq-domain-raised') : null;
          const structure = kind === 'layer' ? panel ? 'content-surface-present-pending' : 'layer-content-surface-missing' : 'leaf-or-shared-structure-pending';
          return [{ index, kind, tag: el.tagName.toLowerCase(), id: el.id, class: className.slice(0, 300),
            declaredComponent: declared, explicitOwner, ownerState: explicitOwner ? 'declared-pending' : 'unknown',
            ownership: declared ? canonicalClasses.length ? 'canonical-declared-pending' : 'declared-missing-canonical' : lqMarked ? 'lq-marked-native' : 'unowned',
            canonicalClasses, structure,
            rendered, inViewport: rendered && rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth,
            disabled: (el as HTMLButtonElement).disabled || el.getAttribute('aria-disabled') === 'true',
            busy: el.getAttribute('aria-busy') === 'true', hidden: !rendered,
            role: el.getAttribute('role'), name: el.getAttribute('aria-label') || el.getAttribute('name') || '',
            nativeForm: (el as HTMLInputElement).form?.id || null,
            state: { expanded: el.getAttribute('aria-expanded'), selected: el.getAttribute('aria-selected'), checked: (el as HTMLInputElement).checked ?? null },
          }];
        });
        return { url: location.href, title: document.title, records, blurHosts, blurTotals: { hosts: blurHosts.length, leaves: blurHosts.filter(host => host.leaf).length }, overflow: document.documentElement.scrollWidth - innerWidth,
          // Diagnostic candidates include intentional scroll areas; only the document overflow is a gate.
          overflowCandidates: overflowCandidates.slice(0, 40),
          totals: { total: records.length, hidden: records.filter(r => r.hidden).length,
            unowned: records.filter(r => r.ownership === 'unowned').length,
            visibleUnowned: records.filter(r => r.ownership === 'unowned' && r.rendered).length,
            invalidDeclaration: records.filter(r => r.ownership === 'declared-missing-canonical').length,
            layerContentMissing: records.filter(r => r.structure === 'layer-content-surface-missing').length,
            missingOwner: records.filter(r => !r.explicitOwner).length },
          limitation: 'DOM declaration/visibility snapshot; no automatic business, visual, accessibility or event-handler acceptance.' };
      });
      const records = result.records.map(record => {
        const classes = record.class.split(/\s+/).filter(cls => !/^(?:lq-|is-|has-)/.test(cls) && cls.includes('-'));
        const candidates = sourceEntries.filter(entry => entry.kind === record.kind && (record.id && entry.id === record.id || classes.some(cls => entry.class.split(/\s+/).includes(cls))));
        return { ...record, sourceMapping: { status: candidates.length === 1 ? 'candidate-pending' : candidates.length ? 'ambiguous-pending' : 'unknown',
          totalCandidates: candidates.length, candidates: candidates.slice(0, 8).map(({ file, line, suggestedOwner, auditId }) => ({ file, line, suggestedOwner, auditId })) } };
      });
      frames.push({ status: 'measured', scope, scopeEvidence: documentContent ? 'Owned material render/preview endpoint; authored learning document content is preserved' : 'Platform page or platform-generated preview chrome', ...result, records });
    } catch (error) {
      frames.push({ status: 'unmeasured', url: frame.url(), reason: String(error) });
    }
  }
  return frames;
}
