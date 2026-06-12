const root = document.querySelector('[data-dashboard-root]');

function parseNumericText(value) {
    const text = String(value || '').trim();
    const match = text.match(/^([^\d.-]*)(-?\d+(?:\.\d+)?)(.*)$/);
    if (!match) {
        return null;
    }
    return {
        prefix: match[1] || '',
        number: Number(match[2]),
        suffix: match[3] || '',
        decimals: match[2].includes('.') ? match[2].split('.')[1].length : 0,
    };
}

function formatNumber(value, decimals) {
    if (!decimals) {
        return String(Math.round(value));
    }
    return value.toFixed(decimals);
}

function animateNumber(node, parsed) {
    if (!Number.isFinite(parsed.number)) {
        return;
    }
    const duration = 420;
    const startedAt = performance.now();
    const render = (timestamp) => {
        const elapsed = Math.min(1, (timestamp - startedAt) / duration);
        const eased = 1 - Math.pow(1 - elapsed, 3);
        node.textContent = `${parsed.prefix}${formatNumber(parsed.number * eased, parsed.decimals)}${parsed.suffix}`;
        if (elapsed < 1) {
            window.requestAnimationFrame(render);
            return;
        }
        node.textContent = `${parsed.prefix}${formatNumber(parsed.number, parsed.decimals)}${parsed.suffix}`;
    };
    node.textContent = `${parsed.prefix}${formatNumber(0, parsed.decimals)}${parsed.suffix}`;
    window.requestAnimationFrame(render);
}

function animateProgress(node) {
    const target = getComputedStyle(node).getPropertyValue('--cockpit-progress').trim() || node.style.width || '0%';
    node.style.width = '0%';
    node.classList.add('is-dashboard-progress-animated');
    window.requestAnimationFrame(() => {
        node.style.width = target;
    });
}

if (root && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    const targets = [
        ...root.querySelectorAll('.student-cockpit [data-count-up]'),
        ...root.querySelectorAll('.student-cockpit [data-progress-bar]'),
    ];
    if (targets.length) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) {
                    return;
                }
                const node = entry.target;
                observer.unobserve(node);
                if (node.matches('[data-progress-bar]')) {
                    animateProgress(node);
                    return;
                }
                const parsed = parseNumericText(node.textContent);
                if (parsed) {
                    animateNumber(node, parsed);
                }
            });
        }, { threshold: 0.32 });
        targets.forEach((node) => observer.observe(node));
    }
}
