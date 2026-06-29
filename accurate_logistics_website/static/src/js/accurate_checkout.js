/** @odoo-module **/

import { rpc } from "@web/core/network/rpc";

/**
 * Accurate Logistics checkout zone picker — self-initializing, no widget.
 *
 * The new website checkout (Interaction `#shop_checkout`) delegates click
 * handlers (carrier select, address card) on the checkout root, and re-renders
 * `#o_delivery_form` on some of them — which would destroy our open <select>.
 * We stop click + mousedown originating inside `.o_accurate_zone_picker` in the
 * CAPTURE phase (before those bubble-phase handlers run), leaving the <select>'s
 * own open/pick behaviour intact.
 */

console.log("[AL] checkout zone script loaded");

function _picker(target) {
    return target && target.closest ? target.closest(".o_accurate_zone_picker") : null;
}

const _stop = (ev) => {
    if (_picker(ev.target)) {
        console.log("[AL] stop", ev.type, "on", ev.target.className);
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
    console.log("[AL] change on", ev.target.className, "value", ev.target.value);
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
        console.log("[AL] filtered sub-zones for zone", zoneId);
    } else if (ev.target.classList.contains("o_accurate_subzone")) {
        if (!subSel.value) {
            return;
        }
        console.log("[AL] saving recipient + repricing…");
        await rpc("/accurate/website/set_recipient", {
            zone_id: zoneSel ? zoneSel.value : false,
            subzone_id: subSel.value,
        });
        window.location.reload();
    }
});
