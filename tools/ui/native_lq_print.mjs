/** Print native LQ modules with local binding shortening only; no bundling or logic transforms. */
import { createRequire } from 'node:module';
import { minifySync } from 'rolldown/experimental';

const require = createRequire(import.meta.url);
export const nativeLqCompiler = `rolldown@${require('rolldown/package.json').version}`;

export function isNativeLqModule(path) {
  return /^js\/lq\/[^/]+\.js$/.test(path)
    || path === 'js/ui_overlay_motion.js' || path === 'js/ui_popover_geometry.js';
}

export function printNativeLqModule(path, source) {
  if (!isNativeLqModule(path)) return source;
  const result = minifySync(path, source, {
    module: true,
    compress: false,
    // Keep module bindings, exports and reflective function/class names stable.
    // Property names and expressions are untouched; compress stays disabled.
    mangle: { toplevel: false, keepNames: true },
    codegen: { legalComments: 'inline', removeWhitespace: true },
  });
  if (result.errors.length) {
    throw new SyntaxError(`Cannot print native LQ module ${path}: ${result.errors.map(error => error.message).join('; ')}`);
  }
  return result.code;
}
