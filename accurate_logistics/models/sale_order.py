from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # ── Accurate Logistics fields ─────────────────────────────────────────────

    accurate_recipient_zone_id = fields.Many2one(
        'accurate.zone',
        string='Recipient Zone',
        # No company selected → empty list. With company → only its zones.
        domain="[('is_subzone', '=', False), ('delivery_company_ids', 'in', [accurate_delivery_company_id])] if accurate_delivery_company_id else [('id', '=', 0)]",
        tracking=True,
        help='Pick a Delivery Company first — this dropdown then shows only that company’s zones.',
    )
    accurate_recipient_subzone_id = fields.Many2one(
        'accurate.zone',
        string='Recipient Sub-zone',
        # No zone selected → empty list. With zone → only its sub-zones.
        domain="[('is_subzone', '=', True), ('parent_id', '=', accurate_recipient_zone_id)] if accurate_recipient_zone_id else [('id', '=', 0)]",
        tracking=True,
        help='Pick a Recipient Zone first — this dropdown then shows only that zone’s sub-zones.',
    )
    accurate_delivery_company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company',
        tracking=True,
        help='The Accurate Logistics delivery company that will handle this order.',
    )

    # ── Shipment classification (passed to the API on dispatch) ────────────
    accurate_type_code = fields.Selection(
        [
            ('FDP', 'Full Package Delivery'),
            ('PDP', 'Partial Package Delivery'),
            ('PTP', 'Package Exchange'),
            ('RTS', 'Return Shipment'),
        ],
        string='Shipment Type', default='FDP', tracking=True,
    )
    accurate_payment_type_code = fields.Selection(
        [
            ('COLC', 'COD – Collect on Delivery'),
            ('CRDT', 'Credit / Postpaid'),
            ('CASH', 'Cash – Already Paid'),
        ],
        string='Payment Type', default='COLC', tracking=True,
    )
    accurate_price_type_code = fields.Selection(
        [
            ('EXCLD', 'Shipping Fee Excluded from Price'),
            ('INCLD', 'Shipping Fee Included in Price'),
        ],
        string='Price Type', default='EXCLD', tracking=True,
    )
    accurate_openable_code = fields.Selection(
        [('Y', 'Yes – Can Open'), ('N', 'No – Cannot Open')],
        string='Openable', default='N', tracking=True,
    )

    # ── Relation to shipments ─────────────────────────────────────────────────

    accurate_shipment_ids = fields.One2many(
        'accurate.shipment', 'sale_id', string='Accurate Shipments'
    )
    accurate_shipment_count = fields.Integer(
        compute='_compute_accurate_shipment_count', string='Shipments'
    )
    accurate_shipment_code = fields.Char(
        compute='_compute_accurate_shipment_summary',
        string='Shipment Code',
        store=True,
    )
    accurate_status_name = fields.Char(
        compute='_compute_accurate_shipment_summary',
        string='Delivery Status',
        store=True,
    )
    accurate_status_code = fields.Char(
        compute='_compute_accurate_shipment_summary',
        string='Delivery Status Code',
        store=True,
    )
    accurate_tracking_url = fields.Char(
        compute='_compute_accurate_shipment_summary',
        string='Tracking URL',
        store=True,
    )

    @api.depends('accurate_shipment_ids')
    def _compute_accurate_shipment_count(self):
        for order in self:
            order.accurate_shipment_count = len(order.accurate_shipment_ids)

    @api.depends(
        'accurate_shipment_ids',
        'accurate_shipment_ids.code',
        'accurate_shipment_ids.api_status_code',
        'accurate_shipment_ids.api_status_name',
        'accurate_shipment_ids.tracking_url',
    )
    def _compute_accurate_shipment_summary(self):
        for order in self:
            # Most recently created shipment as the headline.
            ship = order.accurate_shipment_ids[:1]
            order.accurate_shipment_code = ship.code or ''
            order.accurate_status_name = ship.api_status_name or ''
            order.accurate_status_code = (ship.api_status_code or '').upper()
            order.accurate_tracking_url = ship.tracking_url or ''

    # ── Smart button ──────────────────────────────────────────────────────────

    def action_view_accurate_shipments(self):
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': 'Accurate Shipments',
            'res_model': 'accurate.shipment',
            'view_mode': 'list,form',
            'domain': [('sale_id', '=', self.id)],
            'context': {
                'default_sale_id': self.id,
                'default_delivery_company_id': self.accurate_delivery_company_id.id,
            },
        }
        if self.accurate_shipment_count == 1:
            action['view_mode'] = 'form'
            action['res_id'] = self.accurate_shipment_ids.id
        return action

    # ── Propagate to delivery orders on confirmation ──────────────────────────

    def _action_confirm(self):
        res = super()._action_confirm()
        for order in self:
            if not order.picking_ids:
                continue
            vals = {}
            if order.accurate_recipient_zone_id:
                vals['accurate_recipient_zone_id'] = order.accurate_recipient_zone_id.id
            if order.accurate_recipient_subzone_id:
                vals['accurate_recipient_subzone_id'] = order.accurate_recipient_subzone_id.id
            if order.accurate_delivery_company_id:
                vals['accurate_delivery_company_id'] = order.accurate_delivery_company_id.id
            if order.accurate_type_code:
                vals['accurate_type_code'] = order.accurate_type_code
            if order.accurate_payment_type_code:
                vals['accurate_payment_type_code'] = order.accurate_payment_type_code
            if order.accurate_price_type_code:
                vals['accurate_price_type_code'] = order.accurate_price_type_code
            if order.accurate_openable_code:
                vals['accurate_openable_code'] = order.accurate_openable_code
            if vals:
                order.picking_ids.filtered(
                    lambda p: p.picking_type_code == 'outgoing'
                ).write(vals)
        return res
