import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


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
        domain="[('is_subzone', '=', True), ('parent_id', '=', accurate_recipient_zone_id), ('in_price_list', '=', True), ('delivery_company_ids', 'in', [accurate_delivery_company_id])] if accurate_recipient_zone_id and accurate_delivery_company_id else [('id', '=', 0)]",
        tracking=True,
        help='Pick a Recipient Zone first — this dropdown then shows only that zone’s sub-zones.',
    )
    accurate_delivery_company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company',
        tracking=True,
        help='The Accurate Logistics delivery company that will handle this order.',
    )
    accurate_service_id = fields.Many2one(
        'accurate.service',
        string='Shipping Service',
        tracking=True,
        domain="[('company_id', '=', accurate_delivery_company_id)] if accurate_delivery_company_id else [('id', '=', 0)]",
        help='Which shipping service (Express / Standard / Same-day…) to use. '
             'If empty, the Delivery Company\'s default service will be used '
             'when the shipment is created.',
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
            # 1. Propagate Accurate fields to the dispatch picking
            if order.picking_ids:
                vals = {}
                if order.accurate_recipient_zone_id:
                    vals['accurate_recipient_zone_id'] = order.accurate_recipient_zone_id.id
                if order.accurate_recipient_subzone_id:
                    vals['accurate_recipient_subzone_id'] = order.accurate_recipient_subzone_id.id
                if order.accurate_delivery_company_id:
                    vals['accurate_delivery_company_id'] = order.accurate_delivery_company_id.id
                if order.accurate_service_id:
                    vals['accurate_service_id'] = order.accurate_service_id.id
                if order.accurate_type_code:
                    vals['accurate_type_code'] = order.accurate_type_code
                if order.accurate_payment_type_code:
                    vals['accurate_payment_type_code'] = order.accurate_payment_type_code
                if order.accurate_price_type_code:
                    vals['accurate_price_type_code'] = order.accurate_price_type_code
                if order.accurate_openable_code:
                    vals['accurate_openable_code'] = order.accurate_openable_code
                if vals:
                    # Propagate to the FIRST step of the delivery chain. In a
                    # 2/3-step warehouse the outgoing picking may not exist at
                    # this point (it's created lazily when the Pick is validated),
                    # so we target the Pick step instead. As a fallback, write to
                    # ALL pickings linked to the SO so nothing is missed.
                    dispatch_pickings = order.picking_ids.filtered(
                        lambda p: p._accurate_is_first_in_delivery_chain()
                    )
                    target = dispatch_pickings or order.picking_ids
                    target.write(vals)

            # 2. Auto-create the Accurate shipment as soon as the SO is
            #    confirmed — provided the delivery company + recipient zone
            #    + sub-zone are all set. We don't auto-send if any of those
            #    are missing; the salesperson can still click "Send to
            #    Accurate Logistics" later from the picking once they fill
            #    them in.
            if (
                order.accurate_delivery_company_id
                and order.accurate_recipient_zone_id
                and order.accurate_recipient_subzone_id
                and not order.accurate_shipment_ids
            ):
                try:
                    order._create_accurate_shipment(send_to_api=True)
                except Exception as exc:
                    _logger.warning(
                        'Accurate Logistics: auto-create on SO confirm failed for %s: %s',
                        order.name, exc,
                    )
                    # Don't break SO confirmation if Accurate is unreachable.
                    # The user can retry from the picking form.
        return res

    # ── Helper used by both auto-confirm and the manual picking button ────────

    def _create_accurate_shipment(self, send_to_api=True):
        """Build an `accurate.shipment` from this SO and link it to the
        dispatch picking if one exists. Returns the created shipment.

        If a shipment already exists for this SO, returns it untouched
        (the caller is responsible for showing a 'already exists' popup).
        """
        self.ensure_one()
        if self.accurate_shipment_ids:
            return self.accurate_shipment_ids[:1]

        partner = self.partner_shipping_id or self.partner_id

        def _addr():
            parts = filter(None, [
                partner.street, partner.street2,
                partner.city,
                partner.country_id.name if partner.country_id else None,
            ])
            return ', '.join(parts) or partner.name or ''

        # Locate the dispatch picking (Pick step in 2/3-step setups, otherwise
        # the outgoing). Skipped silently if no picking exists yet.
        dispatch = self.picking_ids.filtered(
            lambda p: p._accurate_is_first_in_delivery_chain()
        )[:1]

        # Decide collection rule based on invoice state:
        #   - No invoice OR invoice not paid → courier collects full amount (COD).
        #   - Invoice paid (incl. in_payment) → send price=0, paymentTypeCode=PAID.
        #   - Invoice partially paid → send remaining residual as price.
        # This lets EzonePay-prepaid orders ship without double-collection.
        price = self.amount_total
        payment_type_code = self.accurate_payment_type_code or 'COLC'
        invoice = self.invoice_ids.filtered(
            lambda i: i.state == 'posted' and i.move_type == 'out_invoice'
        )[:1]
        if invoice:
            payment_state = invoice.payment_state
            if payment_state in ('paid', 'in_payment', 'reversed'):
                price = 0.0
                payment_type_code = 'PAID'
            elif payment_state == 'partial':
                price = invoice.amount_residual

        shipment_vals = {
            'sale_id': self.id,
            'picking_id': dispatch.id if dispatch else False,
            'delivery_company_id': self.accurate_delivery_company_id.id,
            'recipient_name': partner.name or '',
            'recipient_phone': (partner.phone or getattr(partner, 'mobile', '') or ''),
            'recipient_mobile': (getattr(partner, 'mobile', '') or partner.phone or ''),
            'recipient_address': _addr(),
            'recipient_zone_id': self.accurate_recipient_zone_id.id,
            'recipient_subzone_id': self.accurate_recipient_subzone_id.id,
            'ref_number': self.name,
            'price': price,
            'type_code': self.accurate_type_code or 'FDP',
            'payment_type_code': payment_type_code,
            'price_type_code': self.accurate_price_type_code or 'EXCLD',
            'openable_code': self.accurate_openable_code or 'N',
        }
        # Pass the user's chosen Shipping Service if one was picked on the SO;
        # otherwise leave it blank and the shipment's _send_to_api will fall
        # back to the Delivery Company's default service.
        if self.accurate_service_id:
            shipment_vals['service_id'] = self.accurate_service_id.id
        shipment = self.env['accurate.shipment'].create(shipment_vals)

        # Back-link to the picking so the View-Shipment button shows up there.
        if dispatch and not dispatch.accurate_shipment_id:
            dispatch.accurate_shipment_id = shipment.id

        if send_to_api:
            try:
                shipment.action_send_to_api()
            except Exception as exc:
                # Keep the shipment in 'draft'/'error' state so the user can
                # retry from its form. Don't re-raise during SO confirmation.
                _logger.warning(
                    'Accurate Logistics: shipment %s created but send-to-API failed: %s',
                    shipment.name, exc,
                )

        return shipment
