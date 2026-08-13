from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccurateCancellationReason(models.Model):
    _name = 'accurate.cancellation.reason'
    _description = 'Accurate Logistics Cancellation Reason'
    _rec_name = 'name'
    _order = 'company_id, sequence, api_id, name'

    name = fields.Char('Reason', required=True, translate=True)
    code = fields.Char('Code')
    api_id = fields.Integer('API ID', index=True)
    type_code = fields.Char('Type')
    active = fields.Boolean('Active', default=True)
    sequence = fields.Integer(default=10)
    description = fields.Text('Description')
    company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company',
        required=True,
        ondelete='cascade',
        index=True,
        help='Owner Delivery Company. Each merchant account has its own list '
             'of cancellation reasons, so the same api_id can re-occur '
             'across companies.',
    )

    _sql_constraints = []

    @api.constrains('api_id', 'company_id')
    def _check_api_id_unique(self):
        """Reason ids are unique WITHIN a delivery company only."""
        for rec in self:
            if not rec.api_id:
                continue
            duplicate = self.with_context(active_test=False).search([
                ('api_id', '=', rec.api_id),
                ('company_id', '=', rec.company_id.id),
                ('id', '!=', rec.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(
                    'A cancellation reason with API ID %d already exists for '
                    '%s: %s' % (rec.api_id, rec.company_id.name or '?',
                                duplicate.name)
                )

    @api.model
    def _upsert_from_api(self, items, company):
        """Bulk upsert the cancellation reasons OWNED BY *company*.

        Items: list of dicts like {'id': 7, 'code': '7', 'name': '...'}.
        Scoped by company: each merchant account has its own reason list, so
        the same api_id belongs to a different reason per company.
        """
        if not items:
            return {'created': 0, 'updated': 0}
        if not company:
            raise ValueError(
                'A delivery company is required to sync cancellation reasons.')
        api_ids = [int(it['id']) for it in items if it.get('id') is not None]
        domain = [('api_id', 'in', api_ids), ('company_id', '=', company.id)]
        existing = self.with_context(active_test=False).search(domain)
        by_api = {r.api_id: r for r in existing}
        created = 0
        updated = 0
        for it in items:
            api_id = int(it['id']) if it.get('id') is not None else None
            if api_id is None:
                continue
            vals = {
                'api_id': api_id,
                'code': it.get('code') or str(api_id),
                'name': it.get('name') or '',
                'active': True,
            }
            vals['company_id'] = company.id
            rec = by_api.get(api_id)
            if rec:
                rec.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1
        return {'created': created, 'updated': updated}
