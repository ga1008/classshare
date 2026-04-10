(function installES2022Polyfills(globalObject) {
    function defineAt(prototype, resolver) {
        if (!prototype || typeof prototype.at === 'function') {
            return;
        }

        Object.defineProperty(prototype, 'at', {
            value: function at(index) {
                const target = Object(this);
                const length = target.length >>> 0;
                if (length === 0) {
                    return undefined;
                }

                let relativeIndex = Number(index) || 0;
                if (relativeIndex < 0) {
                    relativeIndex += length;
                }

                if (relativeIndex < 0 || relativeIndex >= length) {
                    return undefined;
                }

                return resolver(target, relativeIndex);
            },
            writable: true,
            configurable: true,
        });
    }

    defineAt(Array.prototype, function resolveArrayAt(target, index) {
        return target[index];
    });

    defineAt(String.prototype, function resolveStringAt(target, index) {
        return String(target).charAt(index);
    });
})(typeof globalThis !== 'undefined' ? globalThis : window);
