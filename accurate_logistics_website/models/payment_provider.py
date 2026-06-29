from odoo import fields, models


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    # Add a "Cash on Delivery" flavour to Odoo's offline (custom) provider.
    # Unlike 'wire_transfer', this mode does NOT inject bank-account details
    # into the pending message — the customer simply pays the courier in cash.
    custom_mode = fields.Selection(
        selection_add=[('cash_on_delivery', "Cash on Delivery")],
    )

    def _get_default_payment_method_codes(self):
        """Override to activate the Cash on Delivery payment method when a
        cash-on-delivery custom provider is enabled."""
        codes = super()._get_default_payment_method_codes()
        if self.code == 'custom' and self.custom_mode == 'cash_on_delivery':
            return {'cash_on_delivery'}
        return codes
