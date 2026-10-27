/** Chat attention belongs to SocialClient, including its mute and focus policy. */
export function isSocialChatNotification(notification) {
    return notification?.category === 'social'
        && ['social_message', 'social_mention'].includes(notification?.metadata?.event_type || notification?.metadata?.event);
}
