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

    def _get_shop_payment_values(self, order, **kwargs):
        """Label the main button "Confirm Order" instead of "Pay now" when the
        only available payment options are offline (Cash on Delivery / Wire
        Transfer) — nothing is charged online, the order is just confirmed."""
        values = super()._get_shop_payment_values(order, **kwargs)
        providers = values.get('providers_sudo')
        if providers and all(p.code in ('custom', 'none') for p in providers):
            values['submit_button_label'] = _('Confirm Order')
        return values

    # ── Simplified checkout address: only Name + Phone ────────────────────────
    # The delivery destination is the Accurate Zone / Sub-zone, so the street /
    # city / zip / country fields are unnecessary. We require only name + phone
    # (the rest are hidden by CSS) and default the country to the shop's country
    # so taxes / payment availability still work.

    def shop_address_submit(self, **kw):
        """The simplified address form hides street/city/country, but the page
        still posts them in `required_fields`, which would re-require them and
        reject the submission (HTTP 400). Drop that list so only name + phone
        are required (see _get_mandatory_* below)."""
        kw['required_fields'] = ''
        return super().shop_address_submit(**kw)

    def _get_mandatory_delivery_address_fields(self, country_sudo):
        return {'name', 'phone'}

    def _get_mandatory_billing_address_fields(self, country_sudo):
        return {'name', 'phone'}

    def _parse_form_data(self, form_data):
        address_values, extra_form_data = super()._parse_form_data(form_data)
        if not address_values.get('country_id'):
            country = (
                request.website.company_id.country_id
                or request.env.company.country_id
            )
            if country:
                address_values['country_id'] = country.id
        return address_values, extra_form_data

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
