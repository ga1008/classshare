// Input adapter for the existing ZIP import. Keep source paths and bytes intact.
const MiB = 1024 * 1024;
const MAX_ZIP = 64 * MiB;
const MAX_FILES = 1000;
const MAX_ENTRIES = 2000;
const encoder = new TextEncoder();
const invalidDirectory = '浏览器未提供完整文件夹。请用“选择文件夹”，或选择已打包的 ZIP。';

// The browser protects the drag/clipboard store as soon as the event returns.
// Capture every entry and fallback File before the first asynchronous read.
export function captureGameTransfer(transfer) {
    if (!transfer) return null;
    const files = Array.from(transfer.files || []);
    const items = Array.from(transfer.items || []).filter(item => item.kind === 'file');
    if (items.length > MAX_ENTRIES || files.length > MAX_ENTRIES) return { tooMany: true };
    if (!items.length) {
        if (files.length) return files;
        return Array.from(transfer.types || []).includes('Files') ? { roots: [null] } : null;
    }
    const roots = items.map((item, index) => {
        let fallback = files.length === items.length ? files[index] : null;
        try { fallback = item.getAsFile?.() || fallback; } catch { /* Try the directory APIs. */ }
        try {
            const entry = item.webkitGetAsEntry?.();
            if (entry) return entry.isFile && fallback?.name === entry.name ? fallback : entry;
        } catch { /* Some browsers only expose a clipboard File. */ }
        try {
            if (item.getAsFileSystemHandle) {
                return item.getAsFileSystemHandle().then(handle => handle || fallback, () => fallback);
            }
        } catch { /* Access to the directory API may be disabled. */ }
        return fallback;
    });
    return { roots };
}

function checkAbort(signal) {
    if (signal?.aborted) throw new DOMException('已取消选择。', 'AbortError');
}

async function collect(selection, signal) {
    const roots = selection?.roots || Array.from(selection || []);
    if (selection?.tooMany || roots.length > MAX_ENTRIES) throw new Error('游戏包最多 2000 个文件和目录条目。');
    const files = [];
    let count = 0;
    let total = 0;

    async function visit(node, parent, depth) {
        checkAbort(signal);
        if (!node) throw new Error(invalidDirectory);
        if (++count > MAX_ENTRIES || depth > 17) throw new Error('游戏目录条目过多或层级过深。');
        const path = parent + node.name;
        if (node.isDirectory || node.kind === 'directory') {
            if (node.createReader) {
                const reader = node.createReader();
                while (true) {
                    const children = await new Promise((ok, fail) => reader.readEntries(ok, fail));
                    if (!children.length) break;
                    for (const child of children) await visit(child, path + '/', depth + 1);
                }
            } else {
                for await (const child of node.values()) await visit(child, path + '/', depth + 1);
            }
            return;
        }
        let file = node;
        if (node.isFile) file = await new Promise((ok, fail) => node.file(ok, fail));
        else if (node.kind === 'file') file = await node.getFile();
        if (!(file instanceof Blob)) throw new Error(invalidDirectory);
        const relative = parent ? path : file.webkitRelativePath || path;
        const directZip = roots.length === 1 && !relative.includes('/') && /\.zip$/i.test(file.name);
        if (files.length >= MAX_FILES || file.size > (directZip ? MAX_ZIP : 32 * MiB)) {
            throw new Error(directZip ? '游戏 ZIP 超过 64 MiB。' : '游戏包最多 1000 个文件，每个文件最多 32 MiB。');
        }
        total += file.size;
        if (total > 192 * MiB) throw new Error('游戏目录总大小超过 192 MiB。');
        files.push({ path: relative, file });
    }
    for (const root of roots) await visit(await root, '', 1);
    checkAbort(signal);
    return files;
}

// Match the package validator's portable relative-path contract. Never rename,
// flatten or silently discard source files to resolve a collision.
function members(files) {
    const rootName = files[0]?.path.split('/')[0];
    const wrapped = rootName && files.every(item => item.path.startsWith(rootName + '/'));
    if (wrapped && (rootName === '.' || rootName === '..' || /^[a-z]:$/i.test(rootName) || rootName.includes('\\'))) {
        throw new Error('游戏源文件需要使用相对于所选文件夹的路径。');
    }
    const names = new Map();
    const entries = files.map(({ path, file }) => {
        path = (wrapped ? path.slice(rootName.length + 1) : path).normalize('NFC');
        const parts = path.split('/');
        const encoded = encoder.encode(path);
        if (encoded.length > 240 || parts.length > 16 || /[\\:%#?*"<>|\u0000-\u001f\u007f]/u.test(path) ||
            parts.some(part => !part || part.startsWith('.') || /[ .]$/.test(part) ||
                /^(?:con|nul|aux|prn|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(part))) {
            throw new Error('包含不符合游戏包规范的路径：' + path);
        }
        let prefix = '';
        parts.forEach((part, index) => {
            prefix = prefix ? prefix + '/' + part : part;
            const directory = index < parts.length - 1;
            const key = prefix.toLowerCase();
            const existing = names.get(key);
            if (existing && (existing.path !== prefix || !directory || !existing.directory)) {
                throw new Error('游戏包内的路径重复或大小写冲突：' + prefix);
            }
            names.set(key, { path: prefix, directory });
        });
        return { path, encoded, file };
    });
    if (!entries.some(e => e.path === 'config.json') || !entries.some(e => e.path === 'main.html')) {
        throw new Error('请选择一个同时包含 config.json、main.html 和游戏素材的完整游戏文件夹，或直接选择 ZIP。');
    }
    entries.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
    return { entries, archiveName: (wrapped ? rootName : 'game') + '.zip' };
}

const crcTable = Uint32Array.from({ length: 256 }, (_, n) => {
    for (let bit = 0; bit < 8; bit++) n = (n >>> 1) ^ ((n & 1) ? 0xedb88320 : 0);
    return n;
});

async function encodeMember(file, signal) {
    let checksum = 0xffffffff;
    let compress;
    try { compress = new CompressionStream('deflate-raw'); } catch { /* Stored ZIP is supported by the validator. */ }
    const checked = file.stream().pipeThrough(new TransformStream({
        transform(bytes, controller) {
            checkAbort(signal);
            for (let i = 0; i < bytes.length; i++) checksum = crcTable[(checksum ^ bytes[i]) & 255] ^ (checksum >>> 8);
            controller.enqueue(bytes);
        },
    }), { signal });
    let payload = file;
    if (compress) {
        const compressed = await new Response(checked.pipeThrough(compress, { signal })).blob();
        if (compressed.size < file.size && file.size <= Math.max(1, compressed.size) * 1500) payload = compressed;
    } else {
        await checked.pipeTo(new WritableStream(), { signal });
    }
    return { checksum: (checksum ^ 0xffffffff) >>> 0, payload, method: payload === file ? 0 : 8 };
}

// Standard, non-ZIP64 local headers and central directory. The upload caps
// keep all member offsets and sizes within the ZIP32 range.
function header(central, entry, offset) {
    const bytes = new Uint8Array(central ? 46 : 30);
    const view = new DataView(bytes.buffer);
    view.setUint32(0, central ? 0x02014b50 : 0x04034b50, true);
    if (central) view.setUint16(4, 20, true);
    const start = central ? 6 : 4;
    view.setUint16(start, 20, true);
    view.setUint16(start + 2, 0x0800, true); // UTF-8 paths.
    view.setUint16(start + 4, entry.method, true);
    view.setUint16(start + 8, 0x21, true); // 1980-01-01, deterministic output.
    view.setUint32(start + 10, entry.checksum, true);
    view.setUint32(start + 14, entry.payload.size, true);
    view.setUint32(start + 18, entry.file.size, true);
    view.setUint16(start + 22, entry.encoded.length, true);
    if (central) view.setUint32(42, offset, true);
    return bytes;
}

export async function prepareGamePackage(selection, { signal } = {}) {
    const files = await collect(selection, signal);
    if (files.length === 1 && !files[0].path.includes('/') && /\.zip$/i.test(files[0].path)) return files[0].file;
    if (files.some(item => /\.zip$/i.test(item.path))) throw new Error('请单独选择一个游戏 ZIP，或选择完整的游戏源文件夹。');
    const { entries, archiveName } = members(files);
    const contents = [];
    const directory = [];
    let offset = 0;
    let directorySize = 0;
    for (const entry of entries) {
        checkAbort(signal);
        Object.assign(entry, await encodeMember(entry.file, signal));
        contents.push(header(false, entry, offset), entry.encoded, entry.payload);
        directory.push(header(true, entry, offset), entry.encoded);
        offset += 30 + entry.encoded.length + entry.payload.size;
        directorySize += 46 + entry.encoded.length;
        if (offset + directorySize + 22 > MAX_ZIP) throw new Error('生成的游戏 ZIP 超过 64 MiB。');
    }
    checkAbort(signal);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true);
    end.setUint16(8, entries.length, true);
    end.setUint16(10, entries.length, true);
    end.setUint32(12, directorySize, true);
    end.setUint32(16, offset, true);
    return new File([...contents, ...directory, end.buffer], archiveName, { type: 'application/zip' });
}
