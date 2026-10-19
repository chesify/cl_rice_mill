import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { downloadFile } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

import { ChartCard, Sparkline, seriesColor } from "./chart_card";
import { formatMoney, formatNumber, formatShort, formatValue } from "./format";

const SECTION_TITLES = {
    income: _t("Income & profit"),
    farmers: _t("Farmers & credit"),
    stock: _t("Stock & warehouse"),
    milling: _t("Milling"),
    machines: _t("Machines & fleet"),
    field: _t("Field operations"),
};
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function isoDate(date) {
    const offset = date.getTimezoneOffset() * 60000;
    return new Date(date - offset).toISOString().slice(0, 10);
}

/**
 * Management dashboard of the rice mill: KPI strip, sections A-F and the alerts panel.
 * Every number comes from `cl.rice.dashboard.get_dashboard_data(section, filters)`; every card,
 * bar and row opens the records behind it.
 */
export class RiceDashboard extends Component {
    static template = "cl_rice_mill.Dashboard";
    static components = { ChartCard, Sparkline };
    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.sectionTitles = SECTION_TITLES;
        this.state = proxy({
            meta: null,
            filters: {},
            preset: "season",
            data: {},
            loading: {},
            updated: "",
        });
        onWillStart(async () => {
            const meta = await this.orm.call("cl.rice.dashboard", "get_dashboard_meta", []);
            this.state.meta = meta;
            const saved = meta.saved_filters || {};
            this.state.preset = saved.preset || "season";
            this.state.filters = this.defaultFilters(saved);
            this.loadAll(false);
        });
    }

    // ------------------------------------------------------------------
    // Filters
    // ------------------------------------------------------------------
    defaultFilters(saved = {}) {
        const meta = this.state.meta;
        return {
            season_id: saved.season_id || meta.default_season_id || false,
            date_from: saved.date_from || "",
            date_to: saved.date_to || "",
            field_officer_id: saved.field_officer_id || false,
            farmer_group_id: saved.farmer_group_id || false,
            village: saved.village || "",
            vehicle_id: saved.vehicle_id || false,
            service_type: saved.service_type || "",
            compare: Boolean(saved.compare),
        };
    }

    applyPreset(preset) {
        this.state.preset = preset;
        const today = new Date(this.state.meta.today);
        const filters = this.state.filters;
        if (preset === "this_month") {
            filters.date_from = isoDate(new Date(today.getFullYear(), today.getMonth(), 1));
            filters.date_to = isoDate(today);
        } else if (preset === "last_month") {
            filters.date_from = isoDate(new Date(today.getFullYear(), today.getMonth() - 1, 1));
            filters.date_to = isoDate(new Date(today.getFullYear(), today.getMonth(), 0));
        } else if (preset === "this_quarter") {
            const quarterStart = Math.floor(today.getMonth() / 3) * 3;
            filters.date_from = isoDate(new Date(today.getFullYear(), quarterStart, 1));
            filters.date_to = isoDate(today);
        } else if (preset === "season") {
            filters.date_from = "";
            filters.date_to = "";
        }
        if (preset !== "custom") {
            this.onFiltersChanged();
        }
    }

    setFilter(name, value) {
        const numeric = ["season_id", "field_officer_id", "farmer_group_id", "vehicle_id"];
        this.state.filters[name] = numeric.includes(name) ? parseInt(value) || false : value;
        this.onFiltersChanged();
    }

    onFiltersChanged() {
        const filters = { ...this.state.filters, preset: this.state.preset };
        this.orm.call("cl.rice.dashboard", "save_dashboard_filters", [filters]);
        this.loadAll(false);
    }

    resetFilters() {
        this.state.preset = "season";
        this.state.filters = this.defaultFilters({});
        this.onFiltersChanged();
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    sections() {
        return this.state.meta ? this.state.meta.sections : [];
    }

    hasSection(name) {
        return this.sections().includes(name);
    }

    chartSections() {
        return this.sections().filter((name) => SECTION_TITLES[name]);
    }

    loadAll(force) {
        const filters = { ...this.state.filters };
        for (const section of this.sections()) {
            this.state.loading[section] = true;
            this.orm
                .call("cl.rice.dashboard", "get_dashboard_data", [section, filters], { force })
                .then((data) => {
                    this.state.data[section] = data;
                    this.state.updated = data.updated;
                })
                .catch((error) => {
                    this.state.data[section] = { error: true };
                    throw error;
                })
                .finally(() => {
                    this.state.loading[section] = false;
                });
        }
    }

    refresh() {
        this.loadAll(true);
    }

    data(section) {
        return this.state.data[section] || {};
    }

    isLoading(section) {
        return this.state.loading[section] && !this.state.data[section];
    }

    // ------------------------------------------------------------------
    // Drill-down and exports
    // ------------------------------------------------------------------
    open(act) {
        if (!act) {
            return;
        }
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: act.name,
            res_model: act.model,
            domain: act.domain,
            context: act.context || {},
            views: act.views.map((view) => [false, view]),
            target: "current",
        });
    }

    async exportTable(title, columns, rows) {
        const content = await this.orm.call("cl.rice.dashboard", "export_dashboard_xlsx", [title, columns, rows]);
        const bytes = Uint8Array.from(atob(content), (char) => char.charCodeAt(0));
        await downloadFile(new Blob([bytes], { type: XLSX }), `${title}.xlsx`, XLSX);
    }

    printBoard() {
        window.print();
    }

    // ------------------------------------------------------------------
    // Display helpers
    // ------------------------------------------------------------------
    money(value, short = true) {
        return formatMoney(value, this.state.meta?.currency, short);
    }

    short(value) {
        return formatShort(value);
    }

    number(value, digits = 0) {
        return formatNumber(value, digits);
    }

    kpiValue(kpi) {
        return formatValue(kpi.value, kpi.format, this.state.meta?.currency);
    }

    trendText(kpi) {
        return `${formatNumber(Math.abs(kpi.trend), 1)}%`;
    }

    barStyle(pct) {
        return `width: ${Math.min(pct || 0, 100)}%`;
    }

    trendClass(kpi) {
        if (kpi.trend === null || kpi.trend === undefined) {
            return "";
        }
        const good = ["owed"].includes(kpi.key) ? kpi.trend < 0 : kpi.trend >= 0;
        return good ? "text-success" : "text-danger";
    }

    // ------------------------------------------------------------------
    // Chart configurations
    // ------------------------------------------------------------------
    incomeByLineConfig() {
        const chart = this.data("income").income_by_line;
        return {
            type: "bar",
            stacked: true,
            data: {
                labels: chart.labels,
                datasets: chart.datasets.map((dataset, index) => ({
                    label: dataset.label,
                    data: dataset.data,
                    backgroundColor: seriesColor(index),
                })),
            },
        };
    }

    onIncomePoint(index, datasetIndex) {
        const chart = this.data("income").income_by_line;
        this.open(chart.actions[chart.datasets[datasetIndex].key]);
    }

    costsConfig() {
        const chart = this.data("income").costs;
        return {
            type: "doughnut",
            data: {
                labels: chart.labels,
                datasets: [{ data: chart.data, backgroundColor: chart.data.map((_v, i) => seriesColor(i + 3)) }],
            },
        };
    }

    onCostPoint(index) {
        const chart = this.data("income").costs;
        this.open(chart.actions[chart.keys[index]]);
    }

    waterfallConfig() {
        // floating bars: each step starts where the previous one ended
        const steps = this.data("income").waterfall;
        let running = 0;
        const bars = steps.map((step) => {
            if (step.kind === "total") {
                return [0, step.value];
            }
            const start = running;
            running += step.value;
            return [start, running];
        });
        return {
            type: "bar",
            legend: false,
            data: {
                labels: steps.map((step) => step.label),
                datasets: [
                    {
                        label: _t("Season P&L"),
                        data: bars,
                        backgroundColor: steps.map((step) =>
                            step.kind === "income" ? "#2e7d32" : step.kind === "cost" ? "#c62828" : "#1565c0"
                        ),
                    },
                ],
            },
            options: {
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (context) => `${steps[context.dataIndex].label}: ${formatShort(steps[context.dataIndex].value)}`,
                        },
                    },
                },
            },
        };
    }

    profitTrendConfig() {
        const trend = this.data("income").profit_trend;
        const datasets = [
            {
                label: _t("Profit"),
                data: trend.data,
                borderColor: seriesColor(0),
                backgroundColor: seriesColor(0),
                tension: 0.3,
            },
        ];
        if (trend.previous?.length) {
            datasets.push({
                label: trend.previous_label || _t("Previous season"),
                data: trend.previous,
                borderColor: seriesColor(1),
                backgroundColor: seriesColor(1),
                borderDash: [6, 4],
                tension: 0.3,
            });
        }
        return { type: "line", data: { labels: trend.labels, datasets } };
    }

    topOwedConfig() {
        const rows = this.data("farmers").top_owed;
        return {
            type: "bar",
            legend: false,
            data: {
                labels: rows.map((row) => row.name),
                datasets: [{ label: _t("Owed"), data: rows.map((row) => row.owed), backgroundColor: seriesColor(0) }],
            },
            options: { indexAxis: "y" },
        };
    }

    simpleBarConfig(rows, label, color = 0, horizontal = false) {
        return {
            type: "bar",
            legend: false,
            data: {
                labels: rows.map((row) => row.label),
                datasets: [{ label, data: rows.map((row) => row.value), backgroundColor: seriesColor(color) }],
            },
            options: horizontal ? { indexAxis: "y" } : {},
        };
    }

    partitionConfig() {
        const chart = this.data("stock").partitions;
        return {
            type: "bar",
            stacked: true,
            data: {
                labels: chart.labels,
                datasets: [
                    { label: _t("Farmer-owned"), data: chart.farmer, backgroundColor: seriesColor(0) },
                    { label: _t("Mill-owned"), data: chart.mill, backgroundColor: seriesColor(1) },
                ],
            },
        };
    }

    flowsConfig() {
        const flows = this.data("stock").flows;
        return {
            type: "bar",
            data: {
                labels: flows.labels,
                datasets: [
                    { type: "bar", label: _t("Intake (kg)"), data: flows.intake, backgroundColor: seriesColor(0) },
                    { type: "bar", label: _t("Released (kg)"), data: flows.released, backgroundColor: seriesColor(1) },
                    {
                        type: "line",
                        label: _t("In store (kg)"),
                        data: flows.stock,
                        borderColor: seriesColor(2),
                        backgroundColor: seriesColor(2),
                        tension: 0.3,
                    },
                ],
            },
        };
    }

    onFlowPoint(index, datasetIndex) {
        const actions = this.data("stock").flows.actions;
        this.open(datasetIndex === 1 ? actions.released : actions.intake);
    }

    millingVolumeConfig() {
        const volume = this.data("milling").volume;
        return {
            type: "bar",
            stacked: true,
            data: {
                labels: volume.labels,
                datasets: volume.datasets.map((dataset, index) => ({
                    label: dataset.label,
                    data: dataset.data,
                    backgroundColor: seriesColor(index),
                })),
            },
        };
    }

    recoveryTrendConfig() {
        const trend = this.data("milling").recovery_trend;
        return {
            type: "line",
            legend: false,
            data: {
                labels: trend.labels,
                datasets: [
                    {
                        label: _t("Recovery %"),
                        data: trend.data,
                        borderColor: seriesColor(2),
                        backgroundColor: seriesColor(2),
                        tension: 0.3,
                    },
                ],
            },
        };
    }

    machineProfitConfig() {
        const rows = this.data("machines").machines;
        return {
            type: "bar",
            data: {
                labels: rows.map((row) => row.name),
                datasets: [
                    { label: _t("Income"), data: rows.map((row) => row.income), backgroundColor: "#2e7d32" },
                    {
                        label: _t("Costs"),
                        data: rows.map((row) => row.fuel + row.repairs + row.wages + row.transport),
                        backgroundColor: "#c62828",
                    },
                    { label: _t("Net profit"), data: rows.map((row) => row.net), backgroundColor: "#1565c0" },
                ],
            },
        };
    }

    // ------------------------------------------------------------------
    // Table exports
    // ------------------------------------------------------------------
    exportMachines() {
        const rows = this.data("machines").machines.map((row) => [
            row.name, row.income, row.fuel, row.repairs, row.wages, row.transport, row.net, row.work, row.unit,
            row.cost_per_unit, row.litres_per_acre || 0,
        ]);
        this.exportTable(_t("Machine Profit"), [
            _t("Machine"), _t("Income"), _t("Fuel"), _t("Repairs"), _t("Operator wages"), _t("Staff transport"),
            _t("Net profit"), _t("Work"), _t("Unit"), _t("Cost per unit"), _t("Litres per acre"),
        ], rows);
    }

    exportTopOwed() {
        this.exportTable(_t("Top Farmers Owed"), [_t("Farmer"), _t("Owed"), _t("Collateral cover %")],
            this.data("farmers").top_owed.map((row) => [row.name, row.owed, row.cover]));
    }

    exportOfficers() {
        this.exportTable(_t("Service Jobs per Officer"), [_t("Officer"), _t("Jobs"), _t("Quantity"), _t("Income")],
            this.data("field").per_officer.map((row) => [row.name, row.count, row.quantity, row.income]));
    }

    exportLots() {
        this.exportTable(_t("Oldest Lots"), [_t("Lot"), _t("Owner"), _t("Kg"), _t("Days in store")],
            this.data("stock").oldest_lots.map((row) => [row.lot, row.owner, row.kg, row.days]));
    }
}

registry.category("actions").add("cl_rice_mill.dashboard", RiceDashboard);
