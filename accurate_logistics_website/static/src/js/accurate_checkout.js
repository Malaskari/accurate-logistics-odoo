/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

/**
 * Accurate Logistics checkout zone picker — self-initializing, no widget.
 *
 * The zone picker is rendered once below the delivery-methods list. We still
 * guard against the carrier-row click delegation by stopping click + mousedown
 * that originate inside `.o_accurate_zone_picker` in the CAPTURE phase, leaving
 * the <select>'s own open/pick behaviour intact.
 *
 *  - Zone change    → filter the Sub-zone options client-side (data-parent-id).
 *  - Sub-zone change → POST to /accurate/website/set_recipient, then reload so
 *    the recomputed delivery fee + totals show.
 */

function _picker(target) {
    return target && target.closest ? target.closest(".o_accurate_zone_picker") : null;
}

const _stop = (ev) => {
    if (_picker(ev.target)) {
        ev.stopPropagation();
    }
};
document.addEventListener("click", _stop, true);
document.addEventListener("mousedown", _stop, true);

document.addEventListener("change", async (ev) => {
    const picker = _picker(ev.target);
    if (!picker) {
        return;
    }
    const zoneSel = picker.querySelector("select.o_accurate_zone");
    const subSel = picker.querySelector("select.o_accurate_subzone");
    if (!zoneSel || !subSel) {
        return;
    }

    if (ev.target.classList.contains("o_accurate_zone")) {
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
    } else if (ev.target.classList.contains("o_accurate_subzone")) {
        if (!subSel.value) {
            return;
        }
        await rpc("/accurate/website/set_recipient", {
            zone_id: zoneSel ? zoneSel.value : false,
            subzone_id: subSel.value,
        });
        window.location.reload();
    }
});
