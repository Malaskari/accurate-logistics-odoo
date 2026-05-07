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
    accurate_service_id = fields.Many2one(
        'accurate.service',
        string='Shipping Service',
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

    @api.depends('picking_type_code', 'picking_type_id', 'location_dest_id')
    def _compute_accurate_is_dispatch_step(self):
        for rec in self:
            rec.accurate_is_dispatch_step = rec._accurate_is_first_in_delivery_chain()

    def _accurate_is_first_in_delivery_chain(self):
        """Decide whether THIS picking is the 'first step' of the delivery
        chain — i.e. the one where the Accurate Logistics shipment should be
        created.

        We use the warehouse's `delivery_steps` setting (rather than walking
        move chains, because in Odoo 19 the Ship picking is often created
        lazily when the Pick is validated, so move_dest_ids is empty at the
        time the user is looking at the Pick form):

          - `ship_only` (1-step): the outgoing picking is the dispatch.
          - `pick_ship` (2-step): the picking that ends at the warehouse's
                                   Output location is the dispatch.
          - `pick_pack_ship` (3-step): the picking that ends at the
                                       warehouse's Packing location is the
                                       dispatch (NOT Pack, NOT Ship).
        """
        self.ensure_one()
        wh = self.picking_type_id.warehouse_id
        if not wh:
            # No warehouse (manual picking, transfer between warehouses, etc.)
            # → fall back to outgoing pickings only.
            return self.picking_type_code == 'outgoing'

        steps = wh.delivery_steps
        if steps == 'ship_only':
            return self.picking_type_code == 'outgoing'

        if steps == 'pick_ship':
            # Dispatch = picking that goes Stock → Output
            output_loc = getattr(wh, 'wh_output_stock_loc_id', False)
            return bool(output_loc) and self.location_dest_id == output_loc

        if steps == 'pick_pack_ship':
            # Dispatch = picking that goes Stock → Packing
            pack_loc = getattr(wh, 'wh_pack_stock_loc_id', False)
            return bool(pack_loc) and self.location_dest_id == pack_loc

        # Unknown delivery_steps value → safe default
        return self.picking_type_code == 'outgoing'

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
        """Create a shipment in Accurate Logistics for this delivery — or
        gracefully reuse the one that already exists for the linked Sale Order.
        """
        self.ensure_one()

        # ── Already linked on THIS picking → just open it with a popup ──
        if self.accurate_shipment_id:
            return self._accurate_show_already_exists_popup(self.accurate_shipment_id)

        # ── Already exists on the Sale Order (auto-created at SO confirm,
        #    or created from a sibling picking) → link & open with popup ──
        sale = getattr(self, 'sale_id', False)
        if sale and sale.accurate_shipment_ids:
            existing = sale.accurate_shipment_ids[:1]
            self.accurate_shipment_id = existing.id
            return self._accurate_show_already_exists_popup(existing)

        # ── No existing shipment → validate inputs & create ──
        if not self.accurate_delivery_company_id:
            raise UserError(
                'Please select a Delivery Company before sending to Accurate Logistics.\n'
                'يرجى اختيار شركة الشحن قبل الإرسال إلى أكيوريت لوجيستكس.'
            )
        if not self.accurate_recipient_zone_id or not self.accurate_recipient_subzone_id:
            raise UserError(
                'Please select Recipient Zone and Sub-zone before sending to Accurate Logistics.\n'
                'يرجى اختيار منطقة المستلم والمنطقة الفرعية قبل الإرسال.'
            )

        partner = self.partner_id

        def _addr():
            parts = filter(None, [
                partner.street, partner.street2,
                partner.city, partner.country_id.name,
            ])
            return ', '.join(parts) or partner.name or ''

        weight = getattr(self, 'shipping_weight', 0.0) or getattr(self, 'weight', 0.0) or 0.0
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
        if self.accurate_service_id:
            shipment_vals['service_id'] = self.accurate_service_id.id

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

    def _accurate_show_already_exists_popup(self, shipment):
        """Display a sticky warning notification AND open the existing
        shipment form. Used when the user clicks 'Send to Accurate' but a
        shipment already exists for this Sale Order / picking.
        """
        self.ensure_one()
        ship_label = shipment.code or shipment.name or '—'
        status_label = shipment.api_status_name or shipment.api_status_code or 'pending'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Shipment Already Exists / شحنة موجودة بالفعل',
                'message': (
                    'A shipment was already created for this Sale Order: %s '
                    '(status: %s). Opening the existing shipment instead.\n'
                    'تم إنشاء شحنة بالفعل لأمر البيع هذا: %s (الحالة: %s). '
                    'سيتم فتح الشحنة الموجودة.'
                ) % (ship_label, status_label, ship_label, status_label),
                'type': 'warning',
                'sticky': True,
                'next': {
                    'type': 'ir.actions.act_window',
                    'name': 'Accurate Shipment',
                    'res_model': 'accurate.shipment',
                    'res_id': shipment.id,
                    'view_mode': 'form',
                },
            },
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
