import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # ── Accurate Logistics fields ─────────────────────────────────────────────

    accurate_recipient_zone_id = fields.Many2one(
        'accurate.zone',
        string='Recipient Zone',
        domain="[('is_subzone', '=', False), ('delivery_company_ids', 'in', [accurate_delivery_company_id])] if accurate_delivery_company_id else [('id', '=', 0)]",
    )
    accurate_recipient_subzone_id = fields.Many2one(
        'accurate.zone',
        string='Recipient Sub-zone',
        domain="[('is_subzone', '=', True), ('parent_id', '=', accurate_recipient_zone_id)] if accurate_recipient_zone_id else [('id', '=', 0)]",
    )
    accurate_delivery_company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company (Accurate)',
    )

    # ── Shipment classification (passed to the API on dispatch) ──────────────
    accurate_type_code = fields.Selection(
        [
            ('FDP', 'Full Package Delivery'),
            ('PDP', 'Partial Package Delivery'),
            ('PTP', 'Package Exchange'),
            ('RTS', 'Return Shipment'),
        ],
        string='Shipment Type', default='FDP',
    )
    accurate_payment_type_code = fields.Selection(
        [
            ('COLC', 'COD – Collect on Delivery'),
            ('CRDT', 'Credit / Postpaid'),
            ('CASH', 'Cash – Already Paid'),
        ],
        string='Payment Type', default='COLC',
    )
    accurate_price_type_code = fields.Selection(
        [
            ('EXCLD', 'Shipping Fee Excluded from Price'),
            ('INCLD', 'Shipping Fee Included in Price'),
        ],
        string='Price Type', default='EXCLD',
    )
    accurate_openable_code = fields.Selection(
        [('Y', 'Yes – Can Open'), ('N', 'No – Cannot Open')],
        string='Openable', default='N',
    )

    # ── Related shipment (created after dispatch) ─────────────────────────────

    accurate_shipment_id = fields.Many2one(
        'accurate.shipment',
        string='Accurate Shipment',
        readonly=True,
        copy=False,
        index=True,
    )
    accurate_shipment_code = fields.Char(
        related='accurate_shipment_id.code',
        string='Shipment Code',
        readonly=True,
    )
    accurate_status = fields.Char(
        related='accurate_shipment_id.api_status_name',
        string='Delivery Status',
        readonly=True,
    )
    accurate_tracking_url = fields.Char(
        related='accurate_shipment_id.tracking_url',
        string='Tracking URL',
        readonly=True,
    )

    # ── Validation hook ───────────────────────────────────────────────────────

    def _action_done(self):
        res = super()._action_done()
        auto_create = (
            self.env['ir.config_parameter']
            .sudo()
            .get_param('accurate_logistics.auto_create', 'False')
        )
        if str(auto_create).strip().lower() in ('true', '1'):
            outgoing = self.filtered(
                lambda p: p.picking_type_code == 'outgoing'
                and p.accurate_delivery_company_id
                and not p.accurate_shipment_id
            )
            for picking in outgoing:
                try:
                    picking.action_create_accurate_shipment()
                except Exception as exc:
                    _logger.warning(
                        'Accurate Logistics: auto-create failed for %s: %s',
                        picking.name, exc,
                    )
        return res

    # ── Manual dispatch button ────────────────────────────────────────────────

    def action_create_accurate_shipment(self):
        """Create (or re-create) a shipment in Accurate Logistics for this delivery."""
        self.ensure_one()

        if self.accurate_shipment_id:
            raise UserError(
                'An Accurate Logistics shipment already exists for this delivery: %s'
                % self.accurate_shipment_id.code
            )
        if not self.accurate_delivery_company_id:
            raise UserError(
                'Please select a Delivery Company before sending to Accurate Logistics.'
            )
        if not self.accurate_recipient_zone_id or not self.accurate_recipient_subzone_id:
            raise UserError(
                'Please select Recipient Zone and Sub-zone before sending to Accurate Logistics.'
            )

        partner = self.partner_id

        def _addr():
            parts = filter(None, [
                partner.street, partner.street2,
                partner.city, partner.country_id.name,
            ])
            return ', '.join(parts) or partner.name or ''

        weight = getattr(self, 'shipping_weight', 0.0) or getattr(self, 'weight', 0.0) or 0.0
        sale = getattr(self, 'sale_id', False)
        price = sale.amount_total if sale else 0.0

        shipment_vals = {
            'picking_id': self.id,
            'sale_id': sale.id if sale else False,
            'delivery_company_id': self.accurate_delivery_company_id.id,
            'recipient_name': partner.name or '',
            # Odoo 19 removed res.partner.mobile — fall back gracefully.
            'recipient_phone': (partner.phone or getattr(partner, 'mobile', '') or ''),
            'recipient_mobile': (getattr(partner, 'mobile', '') or partner.phone or ''),
            'recipient_address': _addr(),
            'recipient_zone_id': self.accurate_recipient_zone_id.id,
            'recipient_subzone_id': self.accurate_recipient_subzone_id.id,
            'ref_number': self.name,
            'weight': weight,
            'price': price,
            'type_code': self.accurate_type_code or 'FDP',
            'payment_type_code': self.accurate_payment_type_code or 'COLC',
            'price_type_code': self.accurate_price_type_code or 'EXCLD',
            'openable_code': self.accurate_openable_code or 'N',
        }

        shipment = self.env['accurate.shipment'].create(shipment_vals)
        shipment.action_send_to_api()

        self.accurate_shipment_id = shipment.id

        return {
            'type': 'ir.actions.act_window',
            'name': 'Accurate Shipment',
            'res_model': 'accurate.shipment',
            'res_id': shipment.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_accurate_shipment(self):
        self.ensure_one()
        if not self.accurate_shipment_id:
            raise UserError('No Accurate Logistics shipment linked to this delivery.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Accurate Shipment',
            'res_model': 'accurate.shipment',
            'res_id': self.accurate_shipment_id.id,
            'view_mode': 'form',
        }
