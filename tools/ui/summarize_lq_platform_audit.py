"""Summarize executed DOM observations without promoting them to acceptance."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(directory: Path):
    groups, gaps, controls, blur, overflow, content = [], [], {}, {}, [], []
    execution, execution_failures = None, []
    execution_file = directory / 'playwright-results.json'
    if execution_file.exists():
        run = json.loads(execution_file.read_text(encoding='utf-8'))
        execution = run.get('stats')
        def inspect_suite(suite):
            for spec in suite.get('specs', []):
                for test in spec.get('tests', []):
                    if test.get('status') == 'expected':
                        continue
                    execution_failures.append({'title': spec.get('title'), 'status': test.get('status'),
                        'attempts': [{'status': attempt.get('status'), 'errors': [error.get('message', '') for error in attempt.get('errors', [])]} for attempt in test.get('results', [])]})
            for child in suite.get('suites', []): inspect_suite(child)
        for suite in run.get('suites', []): inspect_suite(suite)
    for file in sorted(directory.glob('*.json')):
        report = json.loads(file.read_text(encoding='utf-8'))
        if not isinstance(report, dict) or 'routesSelected' not in report:
            continue
        group = file.stem
        states = Counter(row['status'] for row in report['results'])
        groups.append({'group': group, 'selected': report['routesSelected'], 'observations': len(report['results']), 'states': dict(states),
                       'sourceChanged': report['sourceChangedDuringRun'], 'pageErrors': report['browserErrors']})
        for row in report['results']:
            url = row.get('requested', row['route']['path'])
            if row['status'] not in ('dom-measured-pending', 'scoped-contract-covered-browser-not-run'):
                gaps.append({'group': group, 'url': url, 'status': row['status'], 'httpStatus': row.get('httpStatus'), 'finalUrl': row.get('finalUrl'), 'reason': row.get('reason', row.get('error'))})
            for frame in row.get('frames', []):
                if frame['status'] != 'measured':
                    gaps.append({'group': group, 'url': url, 'status': 'frame-unmeasured', 'reason': frame.get('reason')})
                    continue
                if frame.get('scope') == 'document-content':
                    content.append({'group': group, 'url': url, 'frame': frame['url'], 'totals': frame['totals'], 'blurTotals': frame['blurTotals'], 'overflow': frame['overflow'], 'scopeEvidence': frame['scopeEvidence']})
                    continue
                if frame['overflow'] > 1:
                    overflow.append({'group': group, 'url': url, 'frame': frame['url'], 'pixels': frame['overflow'], 'candidates': frame.get('overflowCandidates', [])})
                blur[f'{group}:{url}:{frame["url"]}'] = {'group': group, 'url': url, 'frame': frame['url'], **frame['blurTotals'], 'leafHosts': [host for host in frame['blurHosts'] if host['leaf']]}
                for record in frame['records']:
                    issues = []
                    if record['ownership'] == 'unowned': issues.append('unowned')
                    if record['ownership'] == 'declared-missing-canonical': issues.append('invalidDeclaration')
                    if record['structure'] == 'layer-content-surface-missing': issues.append('layerContentMissing')
                    if not issues: continue
                    key = (record['tag'], record['id'], record['class'], tuple(issues))
                    issue = controls.setdefault(key, {**{key: record[key] for key in ['kind', 'tag', 'id', 'class', 'declaredComponent', 'sourceMapping']}, 'issues': issues, 'occurrences': 0, 'visibleOccurrences': 0, 'examples': []})
                    issue['occurrences'] += 1
                    issue['visibleOccurrences'] += int(record['rendered'])
                    if len(issue['examples']) < 8: issue['examples'].append({'group': group, 'url': url, 'frame': frame['url']})
    return {'acceptance': 'pending', 'execution': execution, 'executionFailures': execution_failures, 'groups': groups, 'routeGaps': gaps, 'controlFindings': list(controls.values()), 'overflow': overflow,
            'documentContentFrames': content, 'blur': list(blur.values()), 'summary': {'groups': len(groups), 'routeGaps': len(gaps), 'distinctControlFindings': len(controls), 'overflowFrames': len(overflow),
                                                  'executionFailures': len(execution_failures), 'leafBlurFrames': sum(bool(value['leaves']) for value in blur.values()), 'maximumBlurHosts': max((value['hosts'] for value in blur.values()), default=0)}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = summarize(args.directory)
    if args.output: args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['summary'], ensure_ascii=True, indent=2))
