/** Number formatting of the rice mill dashboards: TZS with separators and short forms (1.25M). */

const SHORT_UNITS = [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
];

export function formatNumber(value, digits = 0) {
    return (value || 0).toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
    });
}

export function formatShort(value) {
    const abs = Math.abs(value || 0);
    for (const [size, unit] of SHORT_UNITS) {
        if (abs >= size) {
            const short = (value / size).toFixed(abs >= size * 100 ? 0 : 2).replace(/\.?0+$/, "");
            return `${short}${unit}`;
        }
    }
    return formatNumber(value);
}

export function formatMoney(value, currency, short = true) {
    const amount = short ? formatShort(value) : formatNumber(value);
    const symbol = currency?.symbol || "TZS";
    return currency?.position === "before" ? `${symbol} ${amount}` : `${amount} ${symbol}`;
}

export function formatValue(value, format, currency) {
    if (format === "money") {
        return formatMoney(value, currency);
    }
    if (format === "kg") {
        return `${formatShort(value)} kg`;
    }
    if (format === "pct") {
        return `${formatNumber(value, 1)}%`;
    }
    return formatNumber(value);
}
