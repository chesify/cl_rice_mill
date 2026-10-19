import { Component, t, useProps } from "@odoo/owl";
import { cookie } from "@web/core/browser/cookie";
import { getColor, getCustomColor } from "@web/core/colors/colors";
import { useChart } from "@web/core/utils/chart_hook";

import { formatShort } from "./format";

export const COLOR_SCHEME = cookie.get("color_scheme");
export const GRID_COLOR = getCustomColor(COLOR_SCHEME, "rgba(0,0,0,.08)", "rgba(255,255,255,.12)");
export const LABEL_COLOR = getCustomColor(COLOR_SCHEME, "#374151", "#E4E4E5");

export function seriesColor(index) {
    return getColor(index, COLOR_SCHEME);
}

/**
 * One Chart.js chart. The parent gives a Chart.js configuration (type, data and options);
 * clicking an element calls `onPoint(index, datasetIndex)` for the drill-down.
 */
export class ChartCard extends Component {
    static template = "cl_rice_mill.ChartCard";
    props = useProps({
        config: t.object(),
        height: t.number().optional(260),
        onPoint: t.function().optional(),
    });
    chart = useChart(() => this.buildConfig());

    buildConfig() {
        const config = this.props.config;
        const horizontal = config.options?.indexAxis === "y";
        const isCircular = ["doughnut", "pie"].includes(config.type);
        const scales = isCircular
            ? {}
            : {
                  x: {
                      stacked: Boolean(config.stacked),
                      grid: { color: GRID_COLOR },
                      ticks: {
                          color: LABEL_COLOR,
                          callback: horizontal ? (value) => formatShort(value) : undefined,
                      },
                  },
                  y: {
                      stacked: Boolean(config.stacked),
                      grid: { color: GRID_COLOR },
                      ticks: {
                          color: LABEL_COLOR,
                          callback: horizontal ? undefined : (value) => formatShort(value),
                      },
                  },
              };
        if (horizontal && !isCircular) {
            scales.y.ticks.callback = function (value) {
                return this.getLabelForValue(value);
            };
        }
        return {
            type: config.type,
            data: config.data,
            options: {
                maintainAspectRatio: false,
                animation: { duration: 300 },
                plugins: {
                    legend: {
                        display: config.legend !== false,
                        position: "bottom",
                        labels: { color: LABEL_COLOR, boxWidth: 12 },
                    },
                    tooltip: {
                        callbacks: {
                            label: (context) => {
                                const value = context.parsed?.y ?? context.parsed;
                                const shown = horizontal ? context.parsed.x : value;
                                return `${context.dataset.label || context.label}: ${formatShort(shown)}`;
                            },
                        },
                    },
                },
                scales,
                onClick: (event, elements) => {
                    if (elements.length && this.props.onPoint) {
                        this.props.onPoint(elements[0].index, elements[0].datasetIndex);
                    }
                },
                onHover: (event, elements) => {
                    event.native.target.style.cursor =
                        elements.length && this.props.onPoint ? "pointer" : "default";
                },
                ...(config.options || {}),
            },
        };
    }
}

/** A small line without axes, under a KPI value. */
export class Sparkline extends Component {
    static template = "cl_rice_mill.Sparkline";
    props = useProps({
        values: t.array(),
        color: t.string().optional(),
    });
    chart = useChart(() => ({
        type: "line",
        data: {
            labels: this.props.values.map((_value, index) => index),
            datasets: [
                {
                    data: this.props.values,
                    borderColor: this.props.color || seriesColor(0),
                    backgroundColor: "transparent",
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.35,
                },
            ],
        },
        options: {
            maintainAspectRatio: false,
            animation: false,
            events: [],
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: { x: { display: false }, y: { display: false } },
        },
    }));
}
