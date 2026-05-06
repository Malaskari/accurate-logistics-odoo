from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class AccurateService(models.Model):
    _name = 'accurate.service'
    _description = 'Accurate Logistics Shipping Service'
    _inherit = ['accurate.api.mixin']
    _rec_name = 'name'
    _order = 'name'

    api_id = fields.Integer('API ID', required=True, index=True, copy=False)
    name = fields.Char('Service Name', required=True)
    active = fields.Boolean('Active', default=True)

    @api.constrains('api_id')
    def _check_api_id_unique(self):
        for rec in self:
            if not rec.api_id:
                continue
            duplicate = self.search([('api_id', '=', rec.api_id), ('id', '!=', rec.id)], limit=1)
            if duplicate:
                raise ValidationError(
                    'A service with API ID %d already exists: %s' % (rec.api_id, duplicate.name)
                )

    def action_sync_services(self):
        """Fetch shipping services from the API and upsert them locally."""
        services = self._al_list_services()
        if not services:
            raise UserError(
                'No shipping services returned from the API. Check your credentials.'
            )

        synced = 0
        for s in services:
            s_id = s.get('id')
            s_name = s.get('name', '')
            if not s_id:
                continue
            existing = self.search([('api_id', '=', s_id)], limit=1)
            vals = {'api_id': s_id, 'name': s_name}
            if existing:
                existing.write(vals)
            else:
                self.create(vals)
            synced += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Services Synced',
                'message': 'Successfully synced %d shipping services.' % synced,
                'type': 'success',
                'sticky': False,
            },
        }
