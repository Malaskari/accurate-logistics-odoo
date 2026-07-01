import logging

from odoo import models

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _post_process(self):
        """Confirm Cash-on-Delivery orders immediately.

        Odoo keeps offline ('custom') transactions in the 'pending' state until
        an operator manually confirms the payment, so the base Sales
        post-processing only marks the quotation as 'sent' — it never confirms
        it (only 'done'/'authorized' transactions reach
        _check_amount_and_confirm_order). For Cash on Delivery there is nothing
        to wait for: the courier collects the cash on delivery. So as soon as a
        COD transaction is registered we confirm the order, which fires
        accurate_logistics' _action_confirm → auto-creates + sends the Accurate
        shipment (Type=FDP, Payment Type=COLC, Price Type=EXCLD, collecting the
        full order total).

        We mirror Odoo's own confirmation call in
        sale/models/payment_transaction._check_amount_and_confirm_order
        (`with_context(send_email=True).action_confirm()`) so the confirmation
        email + downstream flow behave exactly like a normally-confirmed order.
        Failures never break payment post-processing — the operator can still
        confirm the quotation manually.
        """
        res = super()._post_process()
        cod_txs = self.filtered(
            lambda tx: tx.state == 'pending'
            and tx.provider_id.code == 'custom'
            and tx.provider_id.custom_mode == 'cash_on_delivery'
        )
        for tx in cod_txs:
            quotations = tx.sale_order_ids.filtered(
                lambda so: so.state in ('draft', 'sent')
            )
            for order in quotations:
                try:
                    order.with_context(send_email=True).action_confirm()
                except Exception as exc:
                    _logger.warning(
                        'Accurate web (COD): auto-confirm failed for %s: %s',
                        order.name, exc,
                    )
        return res
