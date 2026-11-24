document.addEventListener('click', async event => {
    const trigger = event.target.closest('[data-game-settings]');
    if (!trigger || trigger.disabled) return;
    trigger.disabled = true;
    try {
        const {openClassroomGameSettings} = await import('./classroom-access.js');
        trigger.disabled = false;
        await openClassroomGameSettings(trigger);
    } finally {
        trigger.disabled = false;
    }
});
