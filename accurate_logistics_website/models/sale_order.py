from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def set_delivery_line(self, carrier, amount):
        """When an Accurate carrier is chosen, stamp the order's Accurate
        delivery company + service from the carrier so the existing
        _action_confirm auto-create-shipment has everything it needs."""
        res = super().set_delivery_line(carrier, amount)
        for order in self:
            if (
                carrier
                and carrier.delivery_type == 'accurate'
                and carrier.accurate_delivery_company_id
            ):
                vals = {'accurate_delivery_company_id': carrier.accurate_delivery_company_id.id}
                default_service = carrier.accurate_delivery_company_id.default_service_id
                if not order.accurate_service_id and default_service:
                    vals['accurate_service_id'] = default_service.id
                order.write(vals)
        return res

    def _accurate_collect_amount(self):
        """Amount the courier collects and remits to us (the COD price sent to
        Accurate).

        Two web-specific adjustments on top of the core amount:

        1. PAID ONLINE → 0. A confirmed transaction from a real online provider
           means the order is already prepaid, so the courier collects nothing.

        2. STRIP THE DELIVERY FEE. On the website the courier fee is added to the
           cart as a real delivery line, so amount_total = goods + delivery. But
           the shipment is sent with price_type EXCLD ("shipping fee EXCLUDED
           from price"): Accurate adds its own delivery fee on top of the price
           we send. So the COD price must be GOODS ONLY — otherwise the fee is
           charged twice. The courier then collects goods + fee from the
           customer, keeps the fee, and remits the goods amount to us.
        """
        self.ensure_one()
        paid_online = self.transaction_ids.filtered(
            lambda t: t.state in ('done', 'authorized')
            and t.provider_id.code not in ('custom', 'none')
        )
        if paid_online:
            return 0.0
        amount = super()._accurate_collect_amount()
        delivery_total = sum(
            self.order_line.filtered(lambda l: l.is_delivery).mapped('price_total')
        )
        return max(amount - delivery_total, 0.0)

    def _accurate_set_recipient(self, zone_id, subzone_id):
        """Validate + store the recipient Zone / Sub-zone chosen at checkout.
        Returns True once a valid sub-zone has been stored."""
        self.ensure_one()
        Zone = self.env['accurate.zone']
        zone = Zone.browse(int(zone_id)) if zone_id else Zone
        subzone = Zone.browse(int(subzone_id)) if subzone_id else Zone
        vals = {}
        if zone.exists() and not zone.is_subzone:
            vals['accurate_recipient_zone_id'] = zone.id
        if (
            subzone.exists()
            and subzone.is_subzone
            and subzone.parent_id == zone
            and subzone.in_price_list
        ):
            vals['accurate_recipient_subzone_id'] = subzone.id
        if vals:
            self.write(vals)
        return bool(vals.get('accurate_recipient_subzone_id'))
