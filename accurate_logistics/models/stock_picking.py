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
        domain="[('is_subzone', '=', True), ('parent_id', '=', accurate_recipient_zone_id), ('in_price_list', '=', True)] if accurate_recipient_zone_id else [('id', '=', 0)]",
    )
    accurate_delivery_company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company (Accurate)',
    )
    accurate_service_id = fields.Many2one(
        'accurate.service',
        string='Shipping Service',
        domain="[('company_id', '=', accurate_delivery_company_id)] if accurate_delivery_company_id else [('id', '=', 0)]",
    )

    @api.onchange('accurate_delivery_company_id')
    def _onchange_accurate_delivery_company_id(self):
        for p in self:
            company = p.accurate_delivery_company_id
            if p.accurate_service_id and p.accurate_service_id.company_id != company:
                p.accurate_service_id = False
            if p.accurate_recipient_zone_id and company not in p.accurate_recipient_zone_id.delivery_company_ids:
                p.accurate_recipient_zone_id = False
                p.accurate_recipient_subzone_id = False
            if company and company.default_service_id and not p.accurate_service_id:
                p.accurate_service_id = company.default_service_id

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
    # These resolve the shipment from THIS picking's direct link OR — if not
    # set (e.g. the sibling pick/ship step of a 2-step delivery) — from the
    # Sale Order's shipment, so the code/status/tracking show on EVERY picking
    # in the chain, not only the dispatch one.
    accurate_shipment_code = fields.Char(
        string='Shipment Code',
        compute='_compute_accurate_shipment_info',
        search='_search_accurate_shipment_code',
    )
    accurate_status = fields.Char(
        string='Delivery Status',
        compute='_compute_accurate_shipment_info',
    )
    accurate_tracking_url = fields.Char(
        string='Tracking URL',
        compute='_compute_accurate_shipment_info',
    )

    # Salesperson who created the linked Sale Order — surfaced on the Transfers
    # list so warehouse / dispatch staff can see who sold each order. Stored so
    # it is sortable and group-able directly in the list and search views.
    accurate_salesperson_id = fields.Many2one(
        'res.users',
        string='Salesperson',
        related='sale_id.user_id',
        store=True,
        index=True,
    )

    def _accurate_resolve_shipment(self):
        """Shipment for this picking: direct link first, else the linked
        Sale Order's shipment."""
        self.ensure_one()
        if self.accurate_shipment_id:
            return self.accurate_shipment_id
        sale = getattr(self, 'sale_id', False)
        if sale and sale.accurate_shipment_ids:
            return sale.accurate_shipment_ids[:1]
        return self.env['accurate.shipment']

    @api.depends(
        'accurate_shipment_id',
        'accurate_shipment_id.code',
        'accurate_shipment_id.api_status_name',
        'accurate_shipment_id.tracking_url',
        'sale_id',
        'sale_id.accurate_shipment_ids',
        'sale_id.accurate_shipment_ids.code',
        'sale_id.accurate_shipment_ids.api_status_name',
        'sale_id.accurate_shipment_ids.tracking_url',
    )
    def _compute_accurate_shipment_info(self):
        for rec in self:
            ship = rec._accurate_resolve_shipment()
            rec.accurate_shipment_code = ship.code or ''
            rec.accurate_status = ship.api_status_name or ''
            rec.accurate_tracking_url = ship.tracking_url or ''

    def _search_accurate_shipment_code(self, operator, value):
        """Make the computed shipment code searchable: match pickings linked
        to a shipment whose code matches, either directly or via the SO."""
        shipments = self.env['accurate.shipment'].search([('code', operator, value)])
        if not shipments:
            return [('id', '=', 0)]
        sale_ids = shipments.mapped('sale_id').ids
        return ['|',
                ('accurate_shipment_id', 'in', shipments.ids),
                ('sale_id', 'in', sale_ids)]

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

        # If an INTERNAL step just completed and its shipment is already
        # marked delivered by the courier, validate the outgoing picking that
        # was waiting (the guardrail had skipped it at delivery time because
        # this internal step wasn't done yet).
        for picking in self:
            try:
                picking._accurate_validate_outgoing_if_delivered()
            except Exception as exc:
                _logger.warning(
                    'Accurate Logistics: post-done outgoing validation failed '
                    'for %s: %s', picking.name, exc,
                )
        return res

    def _accurate_validate_outgoing_if_delivered(self):
        """When a non-outgoing picking is validated, if the linked shipment is
        already 'delivered', trigger validation of the still-pending outgoing
        picking via the shipment's own helper."""
        self.ensure_one()
        if self.picking_type_code == 'outgoing':
            return
        sale = getattr(self, 'sale_id', False)
        ship = sale.accurate_shipment_ids[:1] if sale else False
        if not ship and self.accurate_shipment_id:
            ship = self.accurate_shipment_id
        if ship and ship.state == 'delivered':
            ship._validate_delivery_pickings()

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
