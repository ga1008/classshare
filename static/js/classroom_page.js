function initCoursePopover() {
    const popover = document.getElementById('course-info-popover');
    if (!popover) return;

    const overlay = document.getElementById('course-popover-overlay');
    const closeBtn = document.getElementById('course-popover-close');
    const expandBtn = document.getElementById('hero-desc-expand-btn');
    const transitionMs = 280;

    const openPopover = () => {
        popover.hidden = false;
        popover.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-course-popover');
        window.requestAnimationFrame(() => {
            popover.classList.add('popover-open');
        });
    };

    const closePopover = () => {
        popover.classList.remove('popover-open');
        document.body.classList.remove('has-course-popover');
        window.setTimeout(() => {
            if (!popover.classList.contains('popover-open')) {
                popover.hidden = true;
                popover.setAttribute('aria-hidden', 'true');
            }
        }, transitionMs);
    };

    expandBtn?.addEventListener('click', (event) => {
        event.stopPropagation();
        openPopover();
    });

    overlay?.addEventListener('click', closePopover);
    closeBtn?.addEventListener('click', closePopover);

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && popover.classList.contains('popover-open')) {
            closePopover();
        }
    });
}

function initWorkspaceNav() {
    const navLinks = Array.from(document.querySelectorAll('[data-workspace-nav]'));
    if (!navLinks.length) return;

    const navItems = navLinks
        .map((link) => {
            const href = link.getAttribute('href') || '';
            const targetId = href.startsWith('#') ? href.slice(1) : '';
            const section = targetId ? document.getElementById(targetId) : null;
            return section ? { link, targetId, section } : null;
        })
        .filter(Boolean);

    if (!navItems.length) return;

    const setActiveLink = (targetId) => {
        navItems.forEach((item) => {
            item.link.classList.toggle('is-active', item.targetId === targetId);
        });
    };

    navItems.forEach((item) => {
        item.link.addEventListener('click', () => {
            setActiveLink(item.targetId);
        });
    });
    setActiveLink(navItems[0].targetId);
}

function resolveCopyTokens(overrides = {}) {
    const userInfo = window.APP_CONFIG?.userInfo || {};
    const classroom = window.APP_CONFIG?.classroom || {};
    const displayName = String(
        overrides.displayName
        || overrides.display_name
        || document.getElementById('chat-display-name')?.textContent
        || '',
    ).trim();
    const userName = String(userInfo.name || '').trim();
    const aliasOrName = displayName && displayName !== '分配中...' ? displayName : userName;

    return {
        name: userName,
        class_name: String(classroom.class_name || '').trim(),
        course_name: String(classroom.course_name || '').trim(),
        alias_or_name: aliasOrName,
    };
}

function applyCopyTokens(template, tokens) {
    return Object.entries(tokens).reduce((current, [key, value]) => {
        return current.split(`{{${key}}}`).join(String(value || ''));
    }, String(template || ''));
}

function personalizeClassroomCopy(overrides = {}) {
    const tokens = resolveCopyTokens(overrides);
    document.querySelectorAll('[data-copy-template]').forEach((node) => {
        const template = node.getAttribute('data-copy-template');
        if (!template) {
            return;
        }
        node.textContent = applyCopyTokens(template, tokens);
    });
}

export function initClassroomPage() {
    initCoursePopover();
    initWorkspaceNav();
    personalizeClassroomCopy();
    document.addEventListener('classroom:alias-change', (event) => {
        personalizeClassroomCopy(event.detail || {});
    });
}
