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

    # ── First-step detection (multi-step delivery aware) ──────────────────────

    accurate_is_dispatch_step = fields.Boolean(
        compute='_compute_accurate_is_dispatch_step',
        help='True if this is the picking where the Accurate Logistics shipment '
             'should be created. For 1-step delivery: the outgoing picking. '
             'For 2-step (Pick→Ship) or 3-step (Pick→Pack→Ship) delivery: the '
             'FIRST picking in the chain.',
    )

    @api.depends('picking_type_code', 'move_ids', 'move_ids.move_orig_ids', 'move_ids.move_dest_ids')
    def _compute_accurate_is_dispatch_step(self):
        for rec in self:
            rec.accurate_is_dispatch_step = rec._accurate_is_first_in_delivery_chain()

    def _accurate_is_first_in_delivery_chain(self):
        """A picking is the 'first step' if:
          - It has NO predecessor moves (no `move_orig_ids`), AND
          - Its chain of downstream moves eventually ends in an outgoing
            picking (so internal pickings unrelated to delivery are excluded).
        """
        self.ensure_one()
        # No predecessors: this is the first step
        has_predecessor = any(m.move_orig_ids for m in self.move_ids)
        if has_predecessor:
            return False
        # If this is already an outgoing picking, it's a 1-step delivery — show.
        if self.picking_type_code == 'outgoing':
            return True
        # Internal picking: must lead to an outgoing picking somewhere downstream.
        if self.picking_type_code != 'internal':
            return False
        seen = set()
        moves_to_check = self.move_ids
        while moves_to_check:
            next_moves = self.env['stock.move']
            for m in moves_to_check:
                if m.id in seen:
                    continue
                seen.add(m.id)
                if m.picking_id and m.picking_id.picking_type_code == 'outgoing':
                    return True
                next_moves |= m.move_dest_ids
            moves_to_check = next_moves
        return False

    # ── Validation hook ───────────────────────────────────────────────────────

    def _action_done(self):
        res = super()._action_done()
        auto_create = (
            self.env['ir.config_parameter']
            .sudo()
            .get_param('accurate_logistics.auto_create', 'False')
        )
        if str(auto_create).strip().lower() in ('true', '1'):
            # Fire on the FIRST step of the delivery chain — for 2/3-step
            # warehouses this is the internal Pick, not the final Ship.
            to_create = self.filtered(
                lambda p: p._accurate_is_first_in_delivery_chain()
                and p.accurate_delivery_company_id
                and not p.accurate_shipment_id
            )
            for picking in to_create:
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
