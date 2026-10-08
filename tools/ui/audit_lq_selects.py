"""Inventory select declaration/construction sites; never infer runtime acceptance."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

PATTERNS = {
    'native-markup': re.compile(r'<select\b[^>]*>', re.I | re.S),
    'dom-create': re.compile(r'(?:document|doc|ownerDocument|\w+)\.createElement\(\s*[\'"]select[\'"]\s*\)', re.I),
    'shared-factory': re.compile(r'(?<![\w.])(?:createComponent|controlMarkup)\(\s*[\'"]select[\'"]', re.I),
    'field-macro': re.compile(r'\blq_field\([^\n]*?control\s*=\s*[\'"]select[\'"][^\n]*'),
    'selection-macro': re.compile(r'\blq_selection\([^\n]*'),
    'react-native-select': re.compile(r'<LqNativeSelect\b[^>]*>',re.S),
}
BIND = re.compile(r'\b(?:bindSelection|bindDropdown|enhanceDropdowns)\s*\(')

def audit(root: Path):
    entries, files = [], []
    for base in ['templates', 'static/js', 'frontend/src', 'classroom_app/lq_forms.py', 'classroom_app/lq_selection.py']:
        start=root/base
        paths=[start] if start.is_file() else sorted(start.rglob('*'))
        for p in paths:
            if not p.is_file() or p.suffix not in {'.html','.js','.ts','.tsx','.py'} or '.min.' in p.name or 'vendor' in p.parts: continue
            original=p.read_text(encoding='utf-8'); rel=p.relative_to(root).as_posix(); found=[]
            comment = r'\{#.*?#\}|<!--.*?-->' if p.suffix=='.html' else r'/\*.*?\*/'
            source=re.sub(comment,lambda m:''.join('\n' if ch=='\n' else ' ' for ch in m.group(0)),original,flags=re.S)
            if p.suffix in {'.js','.ts','.tsx'}:
                source=re.sub(r'(?m)^\s*//[^\n]*',lambda m:' '*len(m.group(0)),source)
            for kind, pattern in PATTERNS.items():
                for m in pattern.finditer(source):
                    text=m.group(0); line=source.count('\n',0,m.start())+1
                    def attr(name):
                        value=re.search(r'\b'+re.escape(name)+r'\s*=\s*([\'"])(.*?)\1',text,re.S)
                        return value.group(2) if value else None
                    flags=[]
                    if re.search(r'\bmultiple\b',text):flags.append('multiple')
                    if re.search(r'\ssize\s*=',text):flags.append('listbox-size')
                    if not any(x in text for x in ['aria-label','aria-labelledby']) and not attr('id') and kind=='native-markup':flags.append('label-association-needs-review')
                    if '.tsx' in rel:flags.append('react-owned-dom')
                    if 'data-lq-selection' in text:flags.append('explicit-selection')
                    if 'data-lq-dropdown' in text:flags.append('explicit-dropdown')
                    if '{{' in text or '${' in text:flags.append('dynamic-attributes')
                    found.append({'path':rel,'line':line,'kind':kind,'id':attr('id'),'name':attr('name'),'flags':flags,'source':re.sub(r'\s+',' ',text).strip(),'acceptance':'pending-runtime'})
            bindings=[{'line':source.count('\n',0,m.start())+1,'call':m.group(0)} for m in BIND.finditer(source)]
            if found or bindings:
                files.append({'path':rel,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'declarations':len(found),'bindingCalls':bindings,'nativeValueAssignments':len(re.findall(r'\.value\s*=|\.selectedIndex\s*=|\.selected\s*=',source)),'optionsReplacements':len(re.findall(r'\.innerHTML\s*=|\.replaceChildren\(',source))})
                entries.extend(found)
    contracts=['classroom_app/lq_forms.py','classroom_app/lq_selection.py','static/js/lq/forms.js','static/js/lq/selection.js','static/js/lq/dropdown.js','static/js/lq/native-select-observer.js','frontend/src/components/lq-presentation.tsx']
    return {'scope':'Source declarations and construction candidates, not runtime controls or completed migration. Template branches/shared factories are counted separately. React/native/generated content requires owner review.','summary':{'files':len(files),'entries':len(entries),'byKind':dict(Counter(x['kind'] for x in entries)),'byArea':dict(Counter(x['path'].split('/')[0] for x in entries)),'flags':dict(Counter(flag for x in entries for flag in x['flags']))},'files':files,'entries':entries,'contractSources':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in contracts if (root/name).is_file()},'excludedNonProductSources':[{'path':'bidisync.py','reason':'Separate local SSH file-sync maintenance console (--web, port 8765), not a classroom_app product route.'},{'path':'classroom_app/services/academic_course_sync_service.py','reason':'Parses upstream academic HTML; does not render a product select.'}]}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    data=audit(Path.cwd());args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(data['summary'],ensure_ascii=False,indent=2))
