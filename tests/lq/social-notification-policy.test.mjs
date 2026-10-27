import { describe, expect, it } from 'vitest';
import { isSocialChatNotification } from '../../static/js/message_notification_policy.js';

describe('chat popup ownership', () => {
    it('reserves social message and mention popups for SocialClient', () => {
        for (const event_type of ['social_message', 'social_mention']) expect(isSocialChatNotification({ category: 'social', metadata: { event_type } })).toBe(true);
        expect(isSocialChatNotification({ category: 'social', metadata: { event: 'social_message' } })).toBe(true);
    });
    it('preserves friend requests and other category notifications', () => {
        for (const value of [null, {}, { category: 'social' }, { category: 'social', metadata: { event: 'friend_request' } }, { category: 'assignment', metadata: { event: 'social_message' } }]) expect(isSocialChatNotification(value)).toBe(false);
    });
});
