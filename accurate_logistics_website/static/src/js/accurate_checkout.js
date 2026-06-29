/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { rpc } from "@web/core/network/rpc";

/**
 * Accurate Logistics checkout zone picker.
 *
 * The whole delivery-method row in website checkout is click-to-select (clicking
 * it selects that carrier and re-renders the block). That hijacks clicks on our
 * Zone / Sub-zone <select>s, so the dropdown closes before you can pick. We stop
 * click + mousedown that originate inside `.o_accurate_zone_picker` in the
 * CAPTURE phase (top-down), so they never reach the row handler — while leaving
 * the <select>'s own default behaviour (opening + picking) intact.
 *
 *  - Zone change    → filter the Sub-zone options client-side (data-parent-id).
 *  - Sub-zone change → POST the choice to /accurate/website/set_recipient, then
 *    reload so the recomputed delivery fee + totals show.
 */
publicWidget.registry.AccurateCheckoutZones = publicWidget.Widget.extend({
    selector: ".oe_website_sale",
    events: {
        "change select.o_accurate_zone": "_onZoneChange",
        "change select.o_accurate_subzone": "_onSubzoneChange",
    },

    start() {
        this._captureStop = (ev) => {
            const t = ev.target;
            if (t && t.closest && t.closest(".o_accurate_zone_picker")) {
                ev.stopPropagation();
            }
        };
        document.addEventListener("click", this._captureStop, true);
        document.addEventListener("mousedown", this._captureStop, true);
        return this._super(...arguments);
    },

    destroy() {
        if (this._captureStop) {
            document.removeEventListener("click", this._captureStop, true);
            document.removeEventListener("mousedown", this._captureStop, true);
        }
        return this._super(...arguments);
    },

    _picker(target) {
        return target.closest(".o_accurate_zone_picker");
    },

    _filterSubzones(picker) {
        if (!picker) {
            return;
        }
        const zoneSel = picker.querySelector("select.o_accurate_zone");
        const subSel = picker.querySelector("select.o_accurate_subzone");
        if (!zoneSel || !subSel) {
            return;
        }
        const zoneId = zoneSel.value;
        for (const opt of subSel.options) {
            if (!opt.value) {
                continue;
            }
            const match = opt.dataset.parentId === zoneId;
            opt.hidden = !match;
            opt.disabled = !match;
        }
        const current = subSel.selectedOptions[0];
        if (!current || current.hidden) {
            subSel.value = "";
        }
    },

    _onZoneChange(ev) {
        this._filterSubzones(this._picker(ev.target));
    },

    async _onSubzoneChange(ev) {
        const picker = this._picker(ev.target);
        const zoneSel = picker && picker.querySelector("select.o_accurate_zone");
        const subSel = ev.target;
        if (!subSel.value) {
            return;
        }
        await rpc("/accurate/website/set_recipient", {
            zone_id: zoneSel ? zoneSel.value : false,
            subzone_id: subSel.value,
        });
        window.location.reload();
    },
});

export default publicWidget.registry.AccurateCheckoutZones;
