import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class DeliveryCarrier(models.Model):
    _inherit = 'delivery.carrier'

    delivery_type = fields.Selection(
        selection_add=[('accurate', 'Accurate Logistics')],
        ondelete={'accurate': 'set default'},
    )
    accurate_delivery_company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Accurate Delivery Company',
        help='The fixed Accurate Logistics company used to quote and dispatch web '
             'orders placed with this delivery method.',
    )

    # ── Rate (the only hook the website really needs) ─────────────────────────

    def accurate_rate_shipment(self, order):
        """delivery_type='accurate' rate hook.

        Quotes the courier delivery fee for the order's chosen recipient zone /
        sub-zone. If the customer hasn't picked a sub-zone yet the method still
        shows (price 0 + a hint) so they can select it; the price is refreshed
        by the /accurate/website/set_recipient route once they choose.
        """
        self.ensure_one()
        company = self.accurate_delivery_company_id
        if not company:
            return {
                'success': False, 'price': 0.0,
                'error_message': 'This delivery method has no Accurate delivery '
                                 'company configured.',
                'warning_message': False,
            }
        zone = order.accurate_recipient_zone_id
        subzone = order.accurate_recipient_subzone_id
        if not (zone and subzone):
            return {
                'success': True, 'price': 0.0,
                'error_message': False,
                'warning_message': 'Choose your delivery zone to see the fee.',
            }
        return {
            'success': True,
            'price': self._accurate_quote_fee(order, company, zone, subzone),
            'error_message': False, 'warning_message': False,
        }

    def _accurate_quote_fee(self, order, company, zone, subzone):
        """Build fee_input and call the Accurate fee API. Returns 0.0 on any
        problem (mirrors sale_order._accurate_fetch_delivery_fee)."""
        service = order.accurate_service_id or company.default_service_id
        if not (service and zone.api_id and subzone.api_id and service.api_id):
            return 0.0
        # Declared value = goods total only (exclude any delivery line so the
        # fee calc never feeds on itself).
        goods = sum(
            order.order_line.filtered(lambda l: not l.is_delivery).mapped('price_total')
        )
        try:
            weight = order._accurate_order_weight()
        except Exception:
            weight = 1.0
        fee_input = {
            'price': goods or 0.0,
            'weight': weight or 1.0,
            'serviceId': service.api_id,
            'recipientZoneId': zone.api_id,
            'recipientSubzoneId': subzone.api_id,
            'paymentTypeCode': order.accurate_payment_type_code or 'COLC',
            'priceTypeCode': order.accurate_price_type_code or 'EXCLD',
        }
        try:
            fees = company._al_calculate_fees(fee_input)
        except Exception as exc:
            _logger.warning('Accurate web: fee calc failed for %s: %s',
                            order.name or 'cart', exc)
            return 0.0
        return fees.get('delivery') or 0.0

    # ── Ship / cancel / tracking stubs ────────────────────────────────────────
    # The real shipment is created by accurate_logistics on SO confirmation
    # (_action_confirm). These no-op stubs keep the carrier safe even if it is
    # set to "Get Rate and Create Shipment", so Odoo never errors trying to call
    # a missing <type>_send_shipping during delivery validation.

    def accurate_send_shipping(self, pickings):
        return [{
            'exact_price': 0.0,
            'tracking_number': getattr(p, 'accurate_shipment_code', '') or '',
        } for p in pickings]

    def accurate_get_tracking_link(self, picking):
        return getattr(picking, 'accurate_tracking_url', False) or False

    def accurate_cancel_shipment(self, pickings):
        return True
