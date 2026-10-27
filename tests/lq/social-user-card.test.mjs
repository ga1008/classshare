import { describe, expect, it } from 'vitest';
import { avatarIdentity, normalizeCardIdentity, resolveCardTrigger } from '../../static/js/social/user_card_trigger.js';

const BASE = 'http://127.0.0.1:8000/blog';

describe('个人信息面板 trigger identities', () => {
    it('accepts only real student/teacher role:pk identities', () => {
        expect(normalizeCardIdentity('student:12')).toBe('student:12');
        expect(normalizeCardIdentity(' teacher:007 ')).toBe('teacher:7');
        for (const bad of ['off', '', null, undefined, 'assistant:1', 'student:0', 'student:-3', 'student:abc', 'student:1:2', 'pen:abcdef']) {
            expect(normalizeCardIdentity(bad)).toBeNull();
        }
    });

    it('maps profile avatar URLs to identities and ignores self, foreign and content-addressed faces', () => {
        expect(avatarIdentity('/api/profile/avatar?role=student&user_id=41&v=3', BASE)).toBe('student:41');
        expect(avatarIdentity('/api/profile/avatar?role=teacher&user_id=5', BASE)).toBe('teacher:5');
        // The parameterless URL is the signed-in user's own face.
        expect(avatarIdentity('/api/profile/avatar', BASE)).toBeNull();
        expect(avatarIdentity('/api/profile/avatar?role=assistant&user_id=1', BASE)).toBeNull();
        // Blog faces are content addressed (no user id) — a pen name can never resolve.
        expect(avatarIdentity('/api/blog/avatar/abcdef0123', BASE)).toBeNull();
        expect(avatarIdentity('https://evil.example/api/profile/avatar?role=student&user_id=4', BASE)).toBeNull();
        expect(avatarIdentity('', BASE)).toBeNull();
    });

    it('ignores targets without closest()', () => {
        expect(resolveCardTrigger(null)).toBeNull();
        expect(resolveCardTrigger({})).toBeNull();
    });
});
