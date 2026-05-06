from odoo import api, fields, models


class AccurateBranch(models.Model):
    _name = 'accurate.branch'
    _description = 'Accurate Logistics Branch (discovered via API)'
    _rec_name = 'display_name'
    _order = 'company_id, api_id'

    company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company',
        required=True,
        ondelete='cascade',
        index=True,
    )
    api_id = fields.Integer('Branch ID', required=True, index=True)
    sample_zone = fields.Char('Sample Zone Name', readonly=True)
    zone_count = fields.Integer('Zones in Branch', readonly=True)
    display_name = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('api_id', 'sample_zone', 'zone_count')
    def _compute_display_name(self):
        for rec in self:
            label = rec.sample_zone or '—'
            rec.display_name = 'Branch %d — %s (%d zones)' % (
                rec.api_id, label, rec.zone_count or 0,
            )
