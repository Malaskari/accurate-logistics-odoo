from odoo import api, fields, models


class AccurateCancellationReason(models.Model):
    _name = 'accurate.cancellation.reason'
    _description = 'Accurate Logistics Cancellation Reason'
    _rec_name = 'name'
    _order = 'sequence, api_id, name'

    name = fields.Char('Reason', required=True, translate=True)
    code = fields.Char('Code')
    api_id = fields.Integer('API ID', index=True)
    type_code = fields.Char('Type')
    active = fields.Boolean('Active', default=True)
    sequence = fields.Integer(default=10)
    description = fields.Text('Description')

    _sql_constraints = []  # Odoo 19 removed the legacy decorator; explicit unique check below.

    @api.model
    def _upsert_from_api(self, items):
        """Bulk upsert cancellation reasons fetched from the Accurate API.

        Each `items` entry is a dict like:
            {'id': 7, 'code': '7', 'name': 'الزبون رفض الطلبية'}
        """
        if not items:
            return self.browse()
        api_ids = [int(it['id']) for it in items if it.get('id') is not None]
        existing = self.with_context(active_test=False).search(
            [('api_id', 'in', api_ids)]
        )
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
            rec = by_api.get(api_id)
            if rec:
                rec.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1
        return {'created': created, 'updated': updated}
