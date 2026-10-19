import { Component, onWillStart, onWillUpdateProps, proxy, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

import { ChartCard, seriesColor } from "../dashboard/chart_card";
import { formatMoney, formatNumber } from "../dashboard/format";

/** Farmer season smart panel: balance against the limit, charges, stock, collateral and timeline. */
export class FarmerSeasonPanel extends Component {
    static template = "cl_rice_mill.FarmerSeasonPanel";
    static components = { ChartCard };
    props = useProps(standardWidgetProps);

    setup() {
        this.orm = useService("orm");
        this.state = proxy({ data: null });
        onWillStart(() => this.load(this.props.record.resId));
        onWillUpdateProps((nextProps) => {
            if (nextProps.record.resId !== this.props.record.resId) {
                return this.load(nextProps.record.resId);
            }
        });
    }

    async load(resId) {
        this.state.data = resId
            ? await this.orm.call("cl.rice.dashboard", "get_farmer_season_panel", [resId])
            : null;
    }

    money(value) {
        return formatMoney(value, this.state.data?.currency, false);
    }

    number(value, digits = 0) {
        return formatNumber(value, digits);
    }

    gaugeConfig() {
        const data = this.state.data;
        const limit = data.limit || 0;
        const used = Math.min(data.exposure, limit || data.exposure);
        const free = Math.max(limit - used, 0);
        const color = !limit ? seriesColor(0) : data.exposure > limit ? "#c62828" : data.exposure > limit * 0.8 ? "#ef6c00" : "#2e7d32";
        return {
            type: "doughnut",
            legend: false,
            data: {
                labels: [_t("Used"), _t("Available")],
                datasets: [{ data: [used, free], backgroundColor: [color, "rgba(128,128,128,0.2)"], borderWidth: 0 }],
            },
            options: { rotation: -90, circumference: 180, cutout: "70%", plugins: { legend: { display: false } } },
        };
    }

    chargesConfig() {
        const charges = this.state.data.charges;
        return {
            type: "doughnut",
            data: {
                labels: charges.map((charge) => charge.label),
                datasets: [{ data: charges.map((charge) => charge.value), backgroundColor: charges.map((_c, i) => seriesColor(i)) }],
            },
            options: { cutout: "60%" },
        };
    }

    coverClass() {
        const cover = this.state.data.cover;
        if (!this.state.data.balance) {
            return "text-muted";
        }
        return cover < 120 ? "text-danger" : cover <= 200 ? "text-warning" : "text-success";
    }
}

registry.category("view_widgets").add("cl_rice_fs_panel", { component: FarmerSeasonPanel });
