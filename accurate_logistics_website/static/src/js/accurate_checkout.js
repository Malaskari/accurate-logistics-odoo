/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

/**
 * Accurate Logistics checkout zone picker — self-initializing.
 *
 * Uses plain document-level listeners (no publicWidget / wrapper-selector
 * dependency) so it always runs on the checkout page regardless of which
 * container class the website_sale checkout uses.
 *
 *  - The delivery-method row is click-to-select and re-renders on click, which
 *    would close our <select>s. We stop click + mousedown that originate inside
 *    `.o_accurate_zone_picker` in the CAPTURE phase, before that handler runs.
 *  - Zone change → filter Sub-zone options (data-parent-id).
 *  - Sub-zone change → POST to /accurate/website/set_recipient, then reload so
 *    the recomputed delivery fee + totals show.
 */

function _picker(target) {
    return target && target.closest ? target.closest(".o_accurate_zone_picker") : null;
}

// 1) Keep clicks inside the picker from reaching the carrier-row select handler.
const _stop = (ev) => {
    if (_picker(ev.target)) {
        ev.stopPropagation();
    }
};
document.addEventListener("click", _stop, true);
document.addEventListener("mousedown", _stop, true);

// 2) React to zone / sub-zone changes (delegated; change bubbles to document).
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
        // Filter the sub-zone list to the chosen zone.
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
