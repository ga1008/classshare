"""Pure shell fragments; page roots, permissions and business commands stay outside."""
from collections.abc import Mapping
import json
import re
from .lq_components import _url, lq_props as presentation_props

SHELL_KINDS = ('topbar', 'nav_item', 'sidebar', 'dock', 'fab', 'crumbs', 'steps', 'editor', 'page_layout')


def _text(v, required=False):
    if not isinstance(v, str) or (required and not v.strip()):
        raise ValueError('Shell text must be plain text')
    return str(v)


def _key(v):
    v = _text(v, True)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', v) or '--lq-' in v:
        raise ValueError('A stable non-reserved shell id/key is required')
    return v


def _flag(p, key, default=False):
    v = p.get(key, default)
    if not isinstance(v, bool):
        raise ValueError('Shell flags must be boolean')
    return v


def _choice(v, choices):
    if not isinstance(v, str) or v not in choices:
        raise ValueError('Invalid shell variant')
    return v


def _node(tag, attrs=None, children=None):
    return {'tag': tag, 'attrs': attrs or {}, 'children': children or []}


def _slot(name):
    return _node('div', {'class': 'lq-shell-slot', 'data-lq-slot': name}, [{'slot': name}])


def _attrs(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError('Shell attrs must be a mapping')
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not (re.fullmatch(r'(aria|data)-[a-z][a-z0-9_.:-]*', key) or key == 'title'):
            raise ValueError('Unsupported shell attribute')
        if item is None:
            continue
        if not isinstance(item, (str, bool)):
            raise ValueError('Shell attrs must be text or boolean')
        if key.startswith('data-lq-') or key.startswith('aria-') and key != 'aria-describedby':
            continue
        result[key] = str(item).lower() if isinstance(item, bool) else str(item)
    return result


def _items(value, maximum=None):
    if not isinstance(value, (list, tuple)) or maximum is not None and len(value) > maximum:
        raise ValueError('Shell items must be a bounded list')
    seen, result = set(), []
    for item in value:
        if not isinstance(item, Mapping) or set(item) - {'key', 'label', 'href', 'icon', 'current', 'disabled', 'state'}:
            raise ValueError('Invalid shell item')
        key = _key(item.get('key'))
        if key in seen:
            raise ValueError('Duplicate shell item')
        seen.add(key)
        icon = item.get('icon')
        if icon is not None:
            icon = _text(icon, True)
        result.append({'key': key, 'label': _text(item.get('label'), True), 'href': _url(item.get('href')), 'icon': icon,
                       'current': _flag(item, 'current'), 'disabled': _flag(item, 'disabled'),
                       'state': _choice(item.get('state', 'upcoming'), ('complete', 'current', 'upcoming'))})
    if sum(item['current'] for item in result) > 1:
        raise ValueError('Only one current navigation item is allowed')
    return result


def _item(item, class_name='lq-nav-item', command=False):
    a = {'class': class_name, 'data-lq-key': item['key']}
    if item['current']:
        a['aria-current'] = 'page'
    if item['disabled']:
        a['aria-disabled'] = 'true'
    if item['href'] and not item['disabled']:
        tag, a['href'] = 'a', item['href']
    elif command:
        tag, a['type'] = 'button', 'button'
        a['data-lq-command'] = item['key']
        if item['disabled']:
            a['disabled'] = ''
    else:
        tag = 'span'
    children = ([{'icon': item['icon']}] if item['icon'] else []) + [_node('span', {'class': 'lq-nav-item__label'}, [item['label']])]
    return _node(tag, a, children)


def _action(item, prominent=False):
    p = {'label': item['label'], 'icon': item['icon'], 'href': item['href'], 'variant': 'prominent' if prominent else 'glass', 'size': 'sm',
         'disabled': item['disabled'], 'attrs': {'data-lq-command': item['key']}}
    presentation_props('button', **p)
    return {'button': p}


def _pane(identity, key, label, slot):
    return _node('div', {'id': f'{identity}--lq-{key}', 'class': 'lq-shell-pane', 'data-lq-pane': key, 'data-lq-pane-label': label}, [
        _node('div', {'class': 'lq-shell-pane__scrim', 'data-lq-pane-close': '', 'aria-hidden': 'true'}),
        _node('section', {'class': 'lq-shell-pane__surface lq-surface', 'aria-label': label}, [
            _node('header', {'class': 'lq-shell-pane__head'}, [_node('strong', {}, [label]), _node('button', {'type': 'button', 'class': 'lq-shell-pane__close', 'data-lq-pane-close': '', 'aria-label': '关闭' + label}, [{'icon': 'x'}])]), _slot(slot)])])


def _trigger(identity, key, label):
    return _node('button', {'type': 'button', 'class': 'lq-shell-pane__trigger', 'data-lq-pane-open': key, 'aria-controls': f'{identity}--lq-{key}', 'aria-expanded': 'false'}, [label])


def lq_shell_props(component, **p):
    if component not in SHELL_KINDS:
        raise ValueError('Unknown shell component')
    allowed = {
        'topbar': {'title', 'variant', 'back', 'lock_nav', 'actions', 'primary', 'view_transition'},
        'nav_item': {'item'}, 'sidebar': {'label', 'groups', 'persist'},
        'dock': {'label', 'mode', 'items', 'overflow'}, 'fab': {'item', 'size', 'stack_index', 'variant'},
        'crumbs': {'label', 'items'}, 'steps': {'label', 'items'},
        'editor': {'title', 'kind', 'rail_label', 'aside_label', 'primary'}, 'page_layout': {'kind', 'label'},
    }[component] | {'id', 'attrs'}
    if set(p) - allowed:
        raise ValueError('Unknown shell property or raw HTML')
    identity = _key(p.get('id'))
    a = _attrs(p.get('attrs'))
    a.update({'id': identity, 'class': 'lq-' + component.replace('_', '-'), 'data-lq-shell': component})
    if component == 'nav_item':
        result = _item(_items([p.get('item')])[0]); result['attrs'].update(a); return result
    if component == 'fab':
        item = _items([p.get('item')])[0]
        if item['icon'] is None:
            raise ValueError('FAB requires a named icon')
        variant = _choice(p.get('variant', 'glass'), ('glass', 'prominent'))
        a['class'] += (' lq-glass' if variant == 'glass' else ' lq-fab--prominent') + ' lq-fab--' + _choice(p.get('size', 'md'), ('sm', 'md'))
        stack_index = p.get('stack_index', 0)
        if type(stack_index) is not int or stack_index not in (0, 1, 2):
            raise ValueError('FAB stack index must be 0, 1 or 2')
        a['data-lq-fab-slot'] = str(stack_index)
        result = _item(item, command=True); result['attrs'].update(a); result['attrs']['aria-label'] = item['label']; return result
    if component in ('crumbs', 'steps'):
        items = _items(p.get('items', []))
        if not items or component == 'steps' and sum(item['state'] == 'current' for item in items) != 1:
            raise ValueError('Navigation needs items and Steps needs one current step')
        a['aria-label'] = _text(p.get('label', '当前位置' if component == 'crumbs' else '步骤'), True)
        children = []
        for i, item in enumerate(items):
            child = _item(item)
            if component == 'crumbs' and i == len(items) - 1:
                child['attrs']['aria-current'] = 'page'
            if component == 'steps':
                child['attrs'].pop('aria-current', None)
                if item['state'] == 'current':
                    child['attrs']['aria-current'] = 'step'
                child['children'] = [_node('span', {'class': 'lq-steps__node', 'aria-hidden': 'true'}, [{'icon': item['icon']}] if item['icon'] else [str(i + 1)]), _node('span', {'class': 'lq-nav-item__label'}, [item['label']])]
            separator = [_node('span', {'class': 'lq-crumbs__separator', 'aria-hidden': 'true'}, [{'icon': 'chevron-right'}])] if component == 'crumbs' and i < len(items) - 1 else []
            children.append(_node('li', {'data-lq-step': item['state']} if component == 'steps' else {'data-lq-crumb': 'parent' if i == len(items) - 2 else 'current' if i == len(items) - 1 else 'ancestor'}, [child] + separator))
        return _node('nav', a, [_node('ol', {}, children)])
    if component == 'dock':
        mode = _choice(p.get('mode', 'navigation'), ('navigation', 'actions', 'tabs'))
        overflow = _items(p.get('overflow', []), 50)
        items = _items(p.get('items', []), 4 if overflow else 5)
        if set(item['key'] for item in items) & set(item['key'] for item in overflow):
            raise ValueError('Dock item keys must be distinct across overflow')
        if mode == 'tabs' and (items or overflow):
            raise ValueError('Tab Dock takes an existing Tabs slot, not another tab controller')
        if mode == 'navigation' and any(not item['href'] for item in items + overflow):
            raise ValueError('Navigation Dock items need explicit hrefs')
        a.update({'class': 'lq-dock lq-glass', 'data-lq-dock-mode': mode, 'aria-label': _text(p.get('label', '页内操作' if mode == 'actions' else '导航'), True)})
        if mode == 'actions':
            a['role'] = 'group'
        more = [_node('details', {'class': 'lq-dock__more', 'data-lq-dock-more': ''}, [_node('summary', {'class': 'lq-dock__item'}, ['更多']), _node('div', {'class': 'lq-dock__overflow lq-surface', 'data-lq-dock-overflow': ''}, [_item(item, command=mode == 'actions') for item in overflow])])] if overflow else []
        return _node('nav' if mode == 'navigation' else 'div', a, [_item(item, 'lq-dock__item', mode == 'actions') for item in items] + more + [_slot('tabs' if mode == 'tabs' else 'more')])
    if component == 'topbar':
        variant = _choice(p.get('variant', 'standard'), ('standard', 'immersive'))
        actions = _items(p.get('actions', []), 3)
        a.update({'class': f'lq-topbar lq-topbar--{variant} lq-glass lq-scroll-edge', 'data-lq-lock-nav': str(_flag(p, 'lock_nav')).lower(), 'data-lq-view-transition': str(_flag(p, 'view_transition')).lower()})
        lead = [_slot('lead')]
        if p.get('back') is not None:
            back = _items([p['back']])[0]
            if not back['href']:
                raise ValueError('Back navigation needs an href')
            lead.insert(0, _item(back))
        panel = _pane(identity, 'actions', '更多操作', 'more')
        panel['tag'] = 'dialog'
        panel['attrs'].update({'class': 'lq-shell-pane lq-topbar__overflow', 'open': '', 'aria-label': '更多操作'})
        # Inline actions share the topbar material; enhancement adds drawer glass.
        panel['children'][1]['attrs']['class'] = 'lq-shell-pane__surface'
        panel['children'][1]['children'][1] = _node('div', {'class': 'lq-topbar__actions'}, [_action(item) for item in actions] + ([_action(_items([p['primary']])[0], True)] if p.get('primary') is not None else []) + [_slot('more')])
        return _node('header', a, [_node('div', {'class': 'lq-topbar__lead'}, lead), _node('div', {'class': 'lq-topbar__title'}, [_node('h1', {}, [_text(p.get('title'), True)]), _slot('status')]), _trigger(identity, 'actions', '更多'), panel])
    if component == 'sidebar':
        a['class'] += ' lq-sidebar-root'
        label = _text(p.get('label', '工作台导航'), True)
        groups = p.get('groups', [])
        if not isinstance(groups, (list, tuple)) or not groups:
            raise ValueError('Sidebar needs explicit navigation groups')
        seen, keys, details = set(), set(), []
        for group in groups:
            if not isinstance(group, Mapping) or set(group) - {'key', 'label', 'items', 'open'}:
                raise ValueError('Invalid sidebar group')
            key = _key(group.get('key')); items = _items(group.get('items', []))
            if key in seen or any(item['key'] in keys or not item['href'] for item in items):
                raise ValueError('Sidebar needs unique items with explicit hrefs')
            seen.add(key); keys.update(item['key'] for item in items)
            ga = {'class': 'lq-sidebar__group', 'data-lq-nav-group': key}
            if _flag(group, 'open') or any(item['current'] for item in items):
                ga['open'] = ''
            details.append(_node('details', ga, [_node('summary', {}, [_text(group.get('label'), True)]), _node('ul', {}, [_node('li', {'data-lq-nav-search': item['label']}, [_item(item)]) for item in items])]))
        if sum('open' in group['attrs'] for group in details) > 1:
            raise ValueError('Only one sidebar group starts open')
        if p.get('persist') is not None:
            persist = p['persist']
            if not isinstance(persist, Mapping) or set(persist) != {'identity', 'resource', 'key'}:
                raise ValueError('Scoped persistence requires identity/resource/key')
            values = [_text(persist[key], True) for key in ('identity', 'resource', 'key')]
            if any(len(value) > 256 or re.search(r'[\x00-\x1f\x7f]', value) for value in values):
                raise ValueError('Invalid scoped persistence key')
            a['data-lq-persist'] = json.dumps(values, ensure_ascii=False, separators=(',', ':'))
        pane = _pane(identity, 'nav', label, 'user')
        surface = pane['children'][1]
        surface['attrs']['class'] += ' lq-sidebar__surface'
        surface['children'][1:1] = [_slot('brand'), _node('label', {'class': 'lq-sidebar__search'}, [_node('span', {}, ['搜索菜单']), _node('input', {'type': 'search', 'data-lq-nav-search-input': '', 'aria-label': '搜索菜单', 'autocomplete': 'off'})]), _node('nav', {'aria-label': label}, details), _node('p', {'data-lq-nav-empty': '', 'hidden': ''}, ['没有匹配的菜单'])]
        return _node('div', a, [_trigger(identity, 'nav', label), pane])
    if component == 'editor':
        kind = _choice(p.get('kind', 'exam'), ('exam', 'take', 'lesson-plan', 'assessment', 'evaluation'))
        rail, aside = _text(p.get('rail_label', '目录'), True), _text(p.get('aside_label', '属性与预览'), True)
        a.update({'class': 'lq-editor', 'data-lq-editor': kind})
        controls = [_trigger(identity, 'rail', rail), _trigger(identity, 'aside', aside)]
        primary = [_action(_items([p['primary']])[0], True)] if p.get('primary') is not None else []
        return _node('section', a, [_node('header', {'class': 'lq-editor__bar lq-glass'}, [_slot('lead'), _node('h1', {}, [_text(p.get('title'), True)]), _slot('status'), _node('div', {'class': 'lq-editor__controls'}, controls + primary + [_slot('actions')])]), _node('div', {'class': 'lq-editor__workspace'}, [_pane(identity, 'rail', rail, 'rail'), _node('main', {'id': identity + '--lq-main', 'class': 'lq-editor__main lq-surface', 'tabindex': '-1'}, [_slot('main')]), _pane(identity, 'aside', aside, 'aside')]), _node('footer', {'class': 'lq-editor__mobile lq-surface'}, controls + primary)])
    kind = _choice(p.get('kind'), ('list', 'dashboard', 'detail', 'editor', 'take', 'immersive', 'reading'))
    a.update({'class': 'lq-page-layout lq-page-layout--' + kind, 'data-lq-layout': kind, 'aria-label': _text(p.get('label', '页面内容'), True)})
    return _node('section', a, [_slot('head'), _slot('filter'), _node('div', {'class': 'lq-page-layout__body'}, [_node('div', {'class': 'lq-page-layout__main'}, [_slot('main')]), _node('aside', {'class': 'lq-page-layout__aside'}, [_slot('aside')])]), _slot('footer')])
