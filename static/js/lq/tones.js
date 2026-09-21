import { toneLevels } from './tones.generated.js';

/** Resolve a business state without interpreting permissions or transitions.
 * Names used in attributes always come from the generated allowlist. */
export function tone(family, state, { debug = false } = {}) {
    const known = typeof family === 'string' && typeof state === 'string'
        && Object.hasOwn(toneLevels, family) && Object.hasOwn(toneLevels[family], state);
    if (!known && debug) console.debug('Unknown LQ semantic state', family, state);
    return { name: known ? `${family}-${state}` : 'neutral', level: known ? toneLevels[family][state] : 'neutral', known: Boolean(known) };
}
export { toneLevels };
