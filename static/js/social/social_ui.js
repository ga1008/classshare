/**
 * Lazily loaded UI glue for the social client: styles, the 好友 and 聊天
 * modals, the shared image lightbox and the LAN receiver. Loaded on the first
 * click of a 好友 entry (or a toast's 查看), never on page load.
 */
import '../ls_image_lightbox.js';
import { createFriendsModal } from './friends_modal.js';
import { createChatModal } from './chat_modal.js';
import { listenForLan } from './social_lan.js';
import { ensureMarkdown, ensureSocialStyles, formatSize, notify } from './social_dom.js';

/** @param {any} client SocialClient */
export async function createSocialUi(client) {
    await ensureSocialStyles();
    void ensureMarkdown();
    let friends = null;
    let chat = null;
    let lastTrigger = null;

    const openChat = async (key, trigger = null) => {
        if (trigger && !trigger.closest('[data-lq-layer]')) lastTrigger = trigger;
        chat ||= createChatModal(client, { openFriends: (trigger) => openFriends(trigger) });
        await chat.open(key, trigger || lastTrigger);
    };
    const openFriends = (trigger) => {
        friends ||= createFriendsModal(client, { openChat: (key) => openChat(key) });
        friends.open(trigger || lastTrigger);
    };

    listenForLan(client, {
        onIncoming: (info) => notify(`正在通过局域网接收「${info.filename}」（${formatSize(info.size)}）`, 'info', { key: `lan:${info.transferId}`, duration: 4000 }),
        onDone: (info) => {
            if (info.ok) notify(`「${info.filename}」已通过局域网收到。`, 'success', { key: `lan:${info.transferId}` });
        },
    });

    return {
        openFriends,
        openChat,
        chatOpen: () => Boolean(chat?.isOpen()),
    };
}
