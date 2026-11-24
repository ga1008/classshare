// Offsets are relative to the server-issued lease anchor. A retry reuses that
// anchor until the server acknowledges renewal. No timer or network of its own.
export function createPlayClock(now = () => performance.now()) {
    let origin = 0, anchor = 0, started = null, intervals = [];
    function offset() { return Math.min(90000, Math.max(0, Math.floor(now() - origin))); }
    function append(end) {
        if (started === null || end <= started) return;
        intervals.push([started, end]);
        // Bound the payload even when rapidly switching tabs. Never bridge a hidden interval.
        if (intervals.length > 32) {
            let shortest = 0;
            for (let i = 1; i < intervals.length; i++) {
                if (intervals[i][1] - intervals[i][0] < intervals[shortest][1] - intervals[shortest][0]) shortest = i;
            }
            intervals.splice(shortest, 1);
        }
    }
    return {
        reset(expiresAt) {
            anchor = expiresAt;
            origin = now();
            started = null;
            intervals = [];
        },
        active(value) {
            if (value && started === null && anchor) started = offset();
            else if (!value && started !== null) {
                append(offset());
                started = null;
            }
        },
        sample() {
            if (!anchor) return {};
            const end = offset();
            const result = intervals.map(pair => pair.slice());
            if (started !== null && end > started && result.length < 32) result.push([started, end]);
            return {usageAnchor: anchor, playIntervals: result};
        },
    };
}

export function wellbeingSummary(data) {
    if (!data) return '';
    const minutes = Math.floor(data.todaySeconds / 60);
    const points = Number(data.points) || 0;
    return '今日 ' + (minutes ? minutes + ' 分钟' : data.todaySeconds ? '不足 1 分钟' : '0 分钟')
        + ' · 修为 ' + (points > 0 ? '+' : '') + points;
}
