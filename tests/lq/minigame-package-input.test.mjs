import { execFileSync } from 'node:child_process';
import { describe, expect, it, vi } from 'vitest';
import { captureGameTransfer, prepareGamePackage } from '../../static/js/minigames/package-input.js';

function source(path, content = path) {
    const file = new File([content], path.split('/').pop());
    Object.defineProperty(file, 'webkitRelativePath', { value: path });
    return file;
}

async function unpack(zip) {
    const script = [
        'import io, sys, json, zipfile',
        'with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as z:',
        '    assert z.testzip() is None',
        '    print(json.dumps({name:z.read(name).decode() for name in z.namelist()}))',
    ].join('\n');
    return JSON.parse(execFileSync('python', ['-c', script], { input: Buffer.from(await zip.arrayBuffer()) }));
}

describe('minigame source selection', () => {
    it('keeps an existing ZIP and leaves text clipboard events alone', async () => {
        const zip = source('游戏.ZIP');
        const copied = captureGameTransfer({ files: [zip], items: [{ kind: 'file', getAsFile: () => zip }] });
        expect(await prepareGamePackage(copied)).toBe(zip);
        expect(captureGameTransfer({ items: [{ kind: 'string' }], types: ['text/plain'] })).toBeNull();
        await expect(prepareGamePackage(captureGameTransfer({ types: ['Files'] }))).rejects.toThrow('选择文件夹');
    });

    it.each(['native', 'stored'])('round-trips picker files, nesting and UTF-8 names (%s)', async mode => {
        const contents = { 'main.html': '<html>'.repeat(999), 'config.json': '{}', 'static/汉字.txt': '中文内容' };
        if (mode === 'stored') vi.stubGlobal('CompressionStream', undefined);
        try {
            const zip = await prepareGamePackage(Object.entries(contents).map(([p, value]) => source('小游戏/' + p, value)));
            expect(zip.name).toBe('小游戏.zip');
            expect(await unpack(zip)).toEqual(contents);
        } finally { vi.unstubAllGlobals(); }
    });

    it('captures all references before the event ends and drains directory reader batches', async () => {
        let live = true;
        const entry = name => ({ name, isFile: true, file: ok => ok(source(name)) });
        const batches = [[entry('a.js')], [entry('b.js')], []];
        const folder = { name: 'js', isDirectory: true, createReader: () => ({ readEntries: ok => ok(batches.shift()) }) };
        const items = [entry('main.html'), folder, entry('config.json')].map(node =>
            ({ kind: 'file', webkitGetAsEntry: () => live ? node : null }));
        const picked = captureGameTransfer({ items, files: [] });
        live = false;
        const zip = await prepareGamePackage(picked);
        expect(Object.keys(await unpack(zip))).toEqual(['config.json', 'js/a.js', 'js/b.js', 'main.html']);
        expect(batches).toHaveLength(0);
    });

    it('supports async directory handles and cancels before encoding', async () => {
        const handles = ['main.html', 'config.json'].map(name => ({ kind: 'file', name, getFile: async () => source(name) }));
        const dir = { name: 'folder', kind: 'directory', async *values() { yield* handles; } };
        const picked = captureGameTransfer({ items: [{ kind: 'file', getAsFileSystemHandle: async () => dir }] });
        expect((await prepareGamePackage(picked)).name).toBe('folder.zip');
        const abort = new AbortController();
        abort.abort();
        await expect(prepareGamePackage(picked, { signal: abort.signal })).rejects.toMatchObject({ name: 'AbortError' });
    });

    it('rejects missing manifests, collisions, mixed ZIPs, and oversized trees before reading bytes', async () => {
        const base = ['main.html', 'config.json'].map(name => source(name));
        await expect(prepareGamePackage([base[0]])).rejects.toThrow('完整游戏文件夹');
        await expect(prepareGamePackage([...base, source('config.json')])).rejects.toThrow('冲突');
        await expect(prepareGamePackage([...base, source('a.zip')])).rejects.toThrow('单独选择');
        await expect(prepareGamePackage([...base, source('js'), source('js/x.js')])).rejects.toThrow('冲突');
        await expect(prepareGamePackage(Array(1001).fill(base[0]))).rejects.toThrow('1000');
        await expect(prepareGamePackage(['../main.html', '../config.json'].map(name => source(name)))).rejects.toThrow('相对');
    });
});
