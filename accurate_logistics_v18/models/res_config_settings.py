import uuid

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ── Automation ────────────────────────────────────────────────────────────

    accurate_auto_create = fields.Boolean(
        string='Auto-create shipment when Delivery is validated',
        config_parameter='accurate_logistics.auto_create',
        help=(
            'When a delivery order is validated and a Delivery Company is '
            'selected, Odoo will automatically call the Accurate Logistics API '
            'to create the shipment.'
        ),
    )
    accurate_auto_partial = fields.Boolean(
        string='Auto-process partial deliveries',
        config_parameter='accurate_logistics.auto_process_partials',
        default=True,
        help=(
            'When the courier reports a PARTIAL delivery (some products '
            'delivered, some returned), automatically validate the delivered '
            'quantities, create the return picking, invoice only the '
            'delivered part and register the COD payment. When data does not '
            'match (unknown SKU, quantity conflict, already invoiced) the '
            'system never guesses — it notifies staff to process manually.'
        ),
    )

    # ── Webhook ───────────────────────────────────────────────────────────────

    accurate_webhook_secret = fields.Char(
        string='Webhook Secret Token',
        config_parameter='accurate_logistics.webhook_secret',
        help='A secret token used to authenticate incoming webhook calls from Accurate Logistics.',
    )
    accurate_webhook_url = fields.Char(
        string='Webhook URL',
        compute='_compute_webhook_url',
        help=(
            'Copy this URL and paste it as the Callback URL in your '
            'Accurate Logistics account settings.'
        ),
    )

    @api.depends()
    def _compute_webhook_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        secret = self.env['ir.config_parameter'].sudo().get_param(
            'accurate_logistics.webhook_secret', ''
        )
        for rec in self:
            if secret:
                rec.accurate_webhook_url = '%s/accurate/webhook?secret=%s' % (base, secret)
            else:
                rec.accurate_webhook_url = '%s/accurate/webhook' % base

    def action_generate_webhook_secret(self):
        secret = uuid.uuid4().hex
        self.env['ir.config_parameter'].sudo().set_param(
            'accurate_logistics.webhook_secret', secret
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Webhook Secret Generated',
                'message': (
                    'New secret generated. '
                    'Save the settings and copy the Webhook URL to Accurate Logistics.'
                ),
                'type': 'info',
                'sticky': True,
            },
        }
