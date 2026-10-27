import { describe, it, expect } from 'vitest';
import { createHash } from 'node:crypto';
import { Sha256, sha256Hex } from '../../static/js/social/sha256.js';

const node = bytes => createHash('sha256').update(bytes).digest('hex');

describe('social incremental SHA-256', () => {
    it('matches the FIPS vectors', () => {
        expect(sha256Hex('')).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
        expect(sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
        expect(sha256Hex('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq'))
            .toBe('248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1');
    });

    it('is independent of how the input is sliced, around every block boundary', () => {
        const data = new Uint8Array(4099);
        for (let i = 0; i < data.length; i += 1) data[i] = (i * 31 + 7) & 0xff;
        for (const length of [55, 56, 63, 64, 65, 119, 128, 4099]) {
            const expected = node(data.subarray(0, length));
            for (const step of [1, 3, 63, 64, 65, 1000]) {
                const hasher = new Sha256();
                for (let offset = 0; offset < length; offset += step) hasher.update(data.subarray(offset, Math.min(length, offset + step)));
                expect(hasher.hex(), `length ${length} step ${step}`).toBe(expected);
            }
        }
    });

    it('refuses to update or finalize twice', () => {
        const hasher = new Sha256().update(new Uint8Array([1, 2, 3]));
        hasher.hex();
        expect(() => hasher.update(new Uint8Array([4]))).toThrow();
        expect(() => hasher.hex()).toThrow();
    });
});
