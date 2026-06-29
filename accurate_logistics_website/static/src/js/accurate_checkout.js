/** @odoo-module **/

import publicWidget from "@web/legacy/js/public/public_widget";
import { rpc } from "@web/core/network/rpc";

/**
 * Accurate Logistics checkout zone picker.
 *
 * Bound to a stable checkout wrapper and using delegated events, so it works
 * even though the delivery-method block is injected dynamically by website_sale.
 *  - Zone change   → filter the Sub-zone options client-side (data-parent-id).
 *  - Sub-zone change → POST the choice to /accurate/website/set_recipient, then
 *    reload so the recomputed delivery fee + order totals are shown.
 *
 * NOTE (Odoo 18): verify `.oe_website_sale` exists on the checkout page; if the
 * wrapper class differs, adjust the selector below.
 */
publicWidget.registry.AccurateCheckoutZones = publicWidget.Widget.extend({
    selector: ".oe_website_sale",
    events: {
        "change select.o_accurate_zone": "_onZoneChange",
        "change select.o_accurate_subzone": "_onSubzoneChange",
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
        // Reload so website_sale re-renders the delivery price + cart summary
        // with the freshly computed fee.
        window.location.reload();
    },
});

export default publicWidget.registry.AccurateCheckoutZones;
