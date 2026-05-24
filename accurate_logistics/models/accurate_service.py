from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class AccurateService(models.Model):
    _name = 'accurate.service'
    _description = 'Accurate Logistics Shipping Service'
    _inherit = ['accurate.api.mixin']
    _rec_name = 'name'
    _order = 'company_id, name'

    api_id = fields.Integer('API ID', required=True, index=True, copy=False)
    name = fields.Char('Service Name', required=True)
    active = fields.Boolean('Active', default=True)
    company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company',
        ondelete='cascade',
        index=True,
        help='Owner Delivery Company. Each merchant account exposes its own '
             'set of shipping services, so the same api_id can re-occur '
             'across companies.',
    )

    # Reverse side of accurate.zone.service_ids — the zones covered by this
    # service's price list. Lets you see at a glance which zones a service
    # supports, and use the field as a domain source for future per-service
    # zone filtering.
    zone_ids = fields.Many2many(
        'accurate.zone',
        'accurate_service_zone_rel',
        'service_id', 'zone_id',
        string='Zones',
        help='Zones (parent + sub-zones) covered by this service\'s price list.',
    )
    zone_count = fields.Integer('Zone Count', compute='_compute_zone_count')

    @api.depends('zone_ids')
    def _compute_zone_count(self):
        for rec in self:
            rec.zone_count = len(rec.zone_ids)

    @api.constrains('api_id', 'company_id')
    def _check_api_id_unique(self):
        for rec in self:
            if not rec.api_id:
                continue
            duplicate = self.search([
                ('api_id', '=', rec.api_id),
                ('company_id', '=', rec.company_id.id),
                ('id', '!=', rec.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(
                    'A service with API ID %d already exists for this '
                    'Delivery Company: %s' % (rec.api_id, duplicate.name)
                )

    def action_sync_services(self):
        """DEPRECATED — use Test Connection on a Delivery Company.
        Kept for back-compat; iterates all configured companies and re-syncs.
        """
        for company in self.env['accurate.delivery.company'].search([]):
            try:
                company.action_test_connection()
            except Exception:
                pass
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Services Synced',
                'message': 'Re-ran Test Connection on every Delivery Company.',
                'type': 'success',
                'sticky': False,
            },
        }
