import logging

from odoo import _, http
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale

_logger = logging.getLogger(__name__)


class AccurateWebsiteSale(WebsiteSale):

    @http.route(
        '/accurate/website/set_recipient',
        type='json', auth='public', website=True,
    )
    def accurate_set_recipient(self, zone_id=None, subzone_id=None, **kw):
        """Save the chosen Zone / Sub-zone on the cart and re-price the Accurate
        delivery line. Returns the refreshed amounts for the checkout JS."""
        order = request.website.sale_get_order()
        if not order:
            return {'success': False}
        order_sudo = order.sudo()
        ok = order_sudo._accurate_set_recipient(zone_id, subzone_id)

        # Auto-SELECT the Accurate delivery method and (re)price it as soon as a
        # valid zone is chosen — so the fee is applied regardless of whether the
        # customer picked the zone before or after selecting the method.
        carrier = order_sudo.carrier_id
        if not (carrier and carrier.delivery_type == 'accurate'):
            carrier = order_sudo._get_delivery_methods().filtered(
                lambda c: c.delivery_type == 'accurate'
            )[:1]
        if ok and carrier and carrier.delivery_type == 'accurate':
            try:
                rate = carrier.rate_shipment(order_sudo)
                if rate.get('success'):
                    # set_delivery_line sets carrier_id AND creates the fee line.
                    order_sudo.set_delivery_line(carrier, rate['price'])
            except Exception as exc:
                _logger.warning('Accurate web: re-rate failed for %s: %s',
                                order_sudo.name, exc)

        return {
            'success': ok,
            'amount_total': order_sudo.amount_total,
            'amount_delivery': order_sudo.amount_delivery,
            'carrier_id': order_sudo.carrier_id.id,
        }

    def _get_shop_payment_errors(self, order):
        """Block the payment step if the Accurate delivery method is selected but
        the customer hasn't picked a Zone + Sub-zone yet — otherwise the backend
        SO confirmation (_validate_accurate_required_fields) would fail AFTER
        payment."""
        errors = super()._get_shop_payment_errors(order)
        carrier = order.carrier_id
        if (
            carrier
            and carrier.delivery_type == 'accurate'
            and not (order.accurate_recipient_zone_id and order.accurate_recipient_subzone_id)
        ):
            errors.append((
                _('Delivery zone required'),
                _('Please choose your delivery Zone and Sub-zone in the delivery '
                  'step before paying.'),
            ))
        return errors
