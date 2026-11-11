// Palette tokens and the sparkline mark. Colours live in CSS custom properties on
// .viz-root (see templates/dashboard/engagement.html) so light and dark swap in one
// place and nothing here hardcodes a hex value.

const SPARK_WIDTH = 96;
const SPARK_HEIGHT = 28;
const SPARK_PADDING = 3;

export function vizToken(name) {
    const root = document.querySelector('.viz-root') || document.documentElement;
    return getComputedStyle(root).getPropertyValue(name).trim();
}

export function ordinalRamp() {
    return [
        vizToken('--viz-ordinal-1'),
        vizToken('--viz-ordinal-2'),
        vizToken('--viz-ordinal-3'),
        vizToken('--viz-ordinal-4')
    ];
}

function sparkPoints(values) {
    const max = Math.max(...values, 0);
    const min = Math.min(...values, 0);
    const span = max - min || 1;
    const usableWidth = SPARK_WIDTH - SPARK_PADDING * 2;
    const usableHeight = SPARK_HEIGHT - SPARK_PADDING * 2;
    const step = values.length > 1 ? usableWidth / (values.length - 1) : 0;

    return values.map((value, index) => ({
        x: SPARK_PADDING + index * step,
        y: SPARK_PADDING + usableHeight - ((value - min) / span) * usableHeight
    }));
}

/**
 * A trailing-trend sparkline as inline SVG: de-emphasis line, latest point in the accent.
 * Returns '' when there is nothing to plot, so the tile simply omits it.
 */
export function sparkline(values) {
    if (!values || values.length < 2 || values.every(value => !value)) return '';

    const points = sparkPoints(values);
    const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
    const last = points[points.length - 1];

    return `<svg width="${SPARK_WIDTH}" height="${SPARK_HEIGHT}" viewBox="0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}" fill="none">
        <path d="${path}" stroke="${vizToken('--viz-spark')}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="${last.x.toFixed(1)}" cy="${last.y.toFixed(1)}" r="3"
                fill="${vizToken('--viz-spark-accent')}" stroke="${vizToken('--viz-surface')}" stroke-width="2"/>
    </svg>`;
}

/**
 * Percentage change between the last two closed periods.
 * Returns null when there is no prior value to compare against, rather than
 * reporting a meaningless "+100%" against zero.
 */
export function percentDelta(values, {higherIsBetter = true} = {}) {
    if (!values || values.length < 2) return null;
    const current = values[values.length - 1];
    const previous = values[values.length - 2];
    if (!previous) return null;

    const change = ((current - previous) / Math.abs(previous)) * 100;
    if (!Number.isFinite(change) || Math.round(Math.abs(change)) === 0) return null;

    const rising = change > 0;
    return {
        up: higherIsBetter ? rising : !rising,
        // `short` for table rows whose column header already names the period.
        short: `${Math.abs(change).toFixed(0)}%`,
        label: `${Math.abs(change).toFixed(0)}% vs previous month`
    };
}
