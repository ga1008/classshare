// Segmented filter chips that proxy an existing <select>. Lets manage pages
// offer "selection over typing" filters while reusing their current filter
// logic untouched: clicking a chip sets the target select's value and fires a
// native change event, so existing onchange handlers run as before.

function initFilterChips() {
  document.querySelectorAll('[data-filter-chips]').forEach((group) => {
    const targetSelector = group.getAttribute('data-filter-target');
    const target = targetSelector ? document.querySelector(targetSelector) : null;
    if (!target) return;
    const chips = Array.from(group.querySelectorAll('.filter-chip'));
    if (!chips.length) return;

    const sync = () => {
      const value = String(target.value ?? '');
      chips.forEach((chip) => {
        chip.classList.toggle('is-active', chip.getAttribute('data-value') === value);
      });
    };

    chips.forEach((chip) => {
      chip.addEventListener('click', () => {
        const value = chip.getAttribute('data-value') ?? '';
        if (String(target.value ?? '') === value) return;
        target.value = value;
        target.dispatchEvent(new Event('change', { bubbles: true }));
        sync();
      });
    });

    target.addEventListener('change', sync);
    sync();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initFilterChips);
} else {
  initFilterChips();
}
