from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    accurate_delivery_company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company (Accurate)',
        compute='_compute_accurate_delivery_company_id',
        help='Resolved from the linked Sale Order or Accurate shipment, so '
             'the courier logo can be shown on the invoice report.',
    )

    @api.depends('invoice_line_ids', 'invoice_origin')
    def _compute_accurate_delivery_company_id(self):
        for move in self:
            company = self.env['accurate.delivery.company']
            # 1) via the linked Sale Order(s)
            sale = move.line_ids.sale_line_ids.order_id[:1] \
                if 'sale_line_ids' in move.line_ids._fields else False
            if sale and sale.accurate_delivery_company_id:
                company = sale.accurate_delivery_company_id
            # 2) fallback: via the Accurate shipment that booked this invoice
            if not company:
                ship = self.env['accurate.shipment'].search(
                    [('invoice_id', '=', move.id)], limit=1,
                )
                company = ship.delivery_company_id
            move.accurate_delivery_company_id = company
