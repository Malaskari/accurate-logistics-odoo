from odoo import api, fields, models


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
        ondelete='cascade',
        index=True,
        help='Owner Delivery Company. Each merchant account has its own list '
             'of cancellation reasons, so the same api_id can re-occur '
             'across companies.',
    )

    _sql_constraints = []

    @api.model
    def _upsert_from_api(self, items, company=None):
        """Bulk upsert cancellation reasons for a specific Delivery Company.

        Items: list of dicts like {'id': 7, 'code': '7', 'name': '...'}.
        company: accurate.delivery.company record. When given, all upserted
                 records are scoped to that company.
        """
        if not items:
            return {'created': 0, 'updated': 0}
        api_ids = [int(it['id']) for it in items if it.get('id') is not None]
        domain = [('api_id', 'in', api_ids)]
        if company:
            domain.append(('company_id', '=', company.id))
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
            if company:
                vals['company_id'] = company.id
            rec = by_api.get(api_id)
            if rec:
                rec.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1
        return {'created': created, 'updated': updated}
