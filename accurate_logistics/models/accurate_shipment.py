import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccurateShipment(models.Model):
    _name = 'accurate.shipment'
    _description = 'Accurate Logistics Shipment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'
    _order = 'id desc'

    # ── Identity ──────────────────────────────────────────────────────────────

    name = fields.Char(
        'Reference', required=True, copy=False,
        readonly=True, default='New', index=True,
    )
    api_id = fields.Integer('API ID', readonly=True, copy=False, index=True)
    code = fields.Char('Shipment Code', readonly=True, copy=False, index=True)
    ref_number = fields.Char('Your Reference', tracking=True)
    date = fields.Datetime('Shipment Date', default=fields.Datetime.now, tracking=True)
    delivery_date = fields.Date('Expected Delivery', tracking=True)

    # ── Links to Odoo documents ───────────────────────────────────────────────

    sale_id = fields.Many2one(
        'sale.order', string='Sale Order',
        ondelete='set null', index=True, readonly=True,
    )
    picking_id = fields.Many2one(
        'stock.picking', string='Delivery Order',
        ondelete='set null', index=True, readonly=True,
    )
    delivery_company_id = fields.Many2one(
        'accurate.delivery.company', string='Delivery Company',
        ondelete='restrict', index=True, tracking=True,
    )
    invoice_id = fields.Many2one(
        'account.move', string='Invoice',
        readonly=True, copy=False,
    )
    payment_id = fields.Many2one(
        'account.payment', string='Payment',
        readonly=True, copy=False,
    )

    # ── State ─────────────────────────────────────────────────────────────────

    state = fields.Selection(
        [('draft', 'Draft'), ('sent', 'Sent'), ('error', 'Error')],
        default='draft', required=True, copy=False, tracking=True, index=True,
    )
    api_status_code = fields.Char('API Status Code', readonly=True, copy=False)
    api_status_name = fields.Char('API Status', readonly=True, copy=False, tracking=True)
    tracking_url = fields.Char('Tracking URL', readonly=True, copy=False)
    error_message = fields.Text('Last Error', readonly=True, copy=False)

    # ── Recipient ─────────────────────────────────────────────────────────────

    recipient_name = fields.Char('Recipient Name', tracking=True)
    recipient_phone = fields.Char('Recipient Phone', required=True, tracking=True)
    recipient_mobile = fields.Char('Recipient Mobile', required=True, tracking=True)
    recipient_address = fields.Char('Recipient Address', required=True, tracking=True)
    recipient_zone_id = fields.Many2one(
        'accurate.zone', string='Recipient Zone', required=True,
        domain="[('is_subzone', '=', False), ('delivery_company_ids', 'in', [delivery_company_id])] if delivery_company_id else [('id', '=', 0)]",
        tracking=True,
    )
    recipient_subzone_id = fields.Many2one(
        'accurate.zone', string='Recipient Sub-zone', required=True,
        domain="[('is_subzone', '=', True), ('parent_id', '=', recipient_zone_id)] if recipient_zone_id else [('id', '=', 0)]",
        tracking=True,
    )
    recipient_latitude = fields.Float('Lat', digits=(10, 7))
    recipient_longitude = fields.Float('Lng', digits=(10, 7))

    # ── Sender ────────────────────────────────────────────────────────────────

    sender_name = fields.Char('Sender Name')
    sender_phone = fields.Char('Sender Phone')
    sender_mobile = fields.Char('Sender Mobile')
    sender_address = fields.Char('Sender Address')
    sender_postal_code = fields.Char('Sender Postal Code')
    sender_zone_id = fields.Many2one(
        'accurate.zone', string='Sender Zone',
        domain="[('is_subzone', '=', False), ('delivery_company_ids', 'in', [delivery_company_id])] if delivery_company_id else [('id', '=', 0)]",
    )
    sender_subzone_id = fields.Many2one(
        'accurate.zone', string='Sender Sub-zone',
        domain="[('is_subzone', '=', True), ('parent_id', '=', sender_zone_id)] if sender_zone_id else [('id', '=', 0)]",
    )

    # ── Shipment details ──────────────────────────────────────────────────────

    service_id = fields.Many2one('accurate.service', string='Shipping Service', tracking=True)
    type_code = fields.Selection(
        [
            ('FDP', 'Full Package Delivery'),
            ('PDP', 'Partial Package Delivery'),
            ('PTP', 'Package Exchange'),
            ('RTS', 'Return Shipment'),
        ],
        string='Type', tracking=True, default='FDP', required=True,
    )
    payment_type_code = fields.Selection(
        [
            ('COLC', 'COD – Collect on Delivery'),
            ('CRDT', 'Credit / Postpaid'),
            ('CASH', 'Cash – Already Paid'),
        ],
        string='Payment Type', tracking=True, default='COLC', required=True,
    )
    price_type_code = fields.Selection(
        [
            ('EXCLD', 'Shipping Fee Excluded from Price'),
            ('INCLD', 'Shipping Fee Included in Price'),
        ],
        string='Price Type', tracking=True, default='EXCLD', required=True,
    )
    openable_code = fields.Selection(
        [('Y', 'Yes – Can Open'), ('N', 'No – Cannot Open')],
        string='Openable', default='N', required=True,
    )
    weight = fields.Float('Weight (kg)', digits=(10, 3), default=0.5)
    pieces_count = fields.Integer('Pieces', default=1)
    return_pieces_count = fields.Integer('Return Pieces')
    price = fields.Float('Declared Value', digits=(16, 2), tracking=True)
    description = fields.Text('Description')
    notes = fields.Text('Notes')

    # ── Box size ──────────────────────────────────────────────────────────────

    size_length = fields.Float('Length (cm)', digits=(10, 2))
    size_width = fields.Float('Width (cm)', digits=(10, 2))
    size_height = fields.Float('Height (cm)', digits=(10, 2))

    # ── Product lines ─────────────────────────────────────────────────────────

    product_ids = fields.One2many('accurate.shipment.product', 'shipment_id', 'Products')

    # ── Fees (filled from API) ────────────────────────────────────────────────

    fee_amount = fields.Float('Amount', readonly=True, digits=(16, 2))
    fee_delivery = fields.Float('Delivery Fees', readonly=True, digits=(16, 2))
    fee_collection = fields.Float('Collection Fees', readonly=True, digits=(16, 2))
    fee_total = fields.Float('Total', readonly=True, digits=(16, 2), tracking=True)

    # ── ORM ───────────────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = (
                    self.env['ir.sequence'].next_by_code('accurate.shipment') or 'New'
                )
        return super().create(vals_list)

    # ── API dispatch ──────────────────────────────────────────────────────────

    def action_send_to_api(self):
        for rec in self:
            rec._send_to_api()

    def _send_to_api(self):
        self.ensure_one()

        if not (self.recipient_zone_id.api_id and self.recipient_subzone_id.api_id):
            raise UserError(
                'Recipient zone and sub-zone must have valid API IDs. '
                'Go to Accurate Logistics → Configuration → Zones and sync them first.'
            )

        # Resolve a shipping service — required by the API.
        service = self.service_id
        if not service and self.delivery_company_id:
            service = self.delivery_company_id.default_service_id
        if not service:
            service = self.env['accurate.service'].search(
                [('api_id', '!=', False)], limit=1
            )
        if not service or not service.api_id:
            raise UserError(
                'No Shipping Service available. Open the Delivery Company form, '
                'click "Test Connection" to sync services, then set a Default '
                'Shipping Service.'
            )
        if service != self.service_id:
            self.service_id = service.id

        # Bohairat requires these enum codes + weight; the docs don't reveal
        # them as required but the server validates them.
        weight = self.weight or 0.5
        inp = {
            'recipientAddress': self.recipient_address,
            'recipientMobile': self.recipient_mobile,
            'recipientPhone': self.recipient_phone,
            'recipientZoneId': self.recipient_zone_id.api_id,
            'recipientSubzoneId': self.recipient_subzone_id.api_id,
            'serviceId': service.api_id,
            'weight': weight,
            'typeCode': self.type_code or 'FDP',
            'paymentTypeCode': self.payment_type_code or 'COLC',
            'priceTypeCode': self.price_type_code or 'EXCLD',
            'openableCode': self.openable_code or 'N',
        }

        # existing record → update
        if self.api_id:
            inp['id'] = self.api_id
        if self.code:
            inp['code'] = self.code

        def _set(key, val):
            if val:
                inp[key] = val

        _set('refNumber', self.ref_number)
        # Bohairat forbids `date` in input — the server sets it.
        _set('deliveryDate', self.delivery_date and fields.Date.to_string(self.delivery_date))
        _set('recipientName', self.recipient_name)
        _set('recipientLatitude', self.recipient_latitude)
        _set('recipientLongitude', self.recipient_longitude)
        _set('senderName', self.sender_name)
        _set('senderPhone', self.sender_phone)
        _set('senderMobile', self.sender_mobile)
        _set('senderAddress', self.sender_address)
        _set('senderPostalCode', self.sender_postal_code)
        if self.sender_zone_id and self.sender_zone_id.api_id:
            inp['senderZoneId'] = self.sender_zone_id.api_id
        if self.sender_subzone_id and self.sender_subzone_id.api_id:
            inp['senderSubzoneId'] = self.sender_subzone_id.api_id
        _set('piecesCount', self.pieces_count)
        _set('returnPiecesCount', self.return_pieces_count)
        _set('price', self.price)
        _set('description', self.description)
        _set('notes', self.notes)

        if self.size_length or self.size_width or self.size_height:
            inp['size'] = {
                'length': self.size_length,
                'width': self.size_width,
                'height': self.size_height,
            }
        if self.product_ids:
            inp['shipmentProducts'] = [
                {'name': p.name, 'quantity': p.quantity, 'price': p.price}
                for p in self.product_ids
            ]

        if not self.delivery_company_id:
            raise UserError('No Delivery Company selected. Cannot send to API.')

        try:
            result = self.delivery_company_id._al_save_shipment(inp)
        except Exception as exc:
            self.write({'state': 'error', 'error_message': str(exc)})
            raise

        self._apply_api_response(result)
        self.write({'state': 'sent', 'error_message': False})
        self.message_post(
            body='Shipment sent to Accurate Logistics. Code: <b>%s</b>' % (self.code or '—')
        )

    # ── Apply API response ────────────────────────────────────────────────────

    def _apply_api_response(self, data):
        if not data:
            return
        vals = {}
        if data.get('id'):
            vals['api_id'] = data['id']
        if data.get('code'):
            vals['code'] = data['code']
        if data.get('refNumber'):
            vals['ref_number'] = data['refNumber']
        if data.get('trackingUrl'):
            vals['tracking_url'] = data['trackingUrl']
        if data.get('status'):
            vals['api_status_code'] = data['status'].get('code', '')
            vals['api_status_name'] = data['status'].get('name', '')
        for src, dst in [
            ('amount', 'fee_amount'),
            ('deliveryFees', 'fee_delivery'),
            ('collectionFees', 'fee_collection'),
            ('totalAmount', 'fee_total'),
        ]:
            if data.get(src) is not None:
                vals[dst] = data[src]
        if vals:
            self.write(vals)

    # ── Status sync ───────────────────────────────────────────────────────────

    def action_sync_status(self):
        for rec in self:
            if not rec.api_id and not rec.code:
                raise UserError('Shipment has not been sent to the API yet.')
            if not rec.delivery_company_id:
                raise UserError('No Delivery Company linked to this shipment.')
            data = rec.delivery_company_id._al_get_shipment(api_id=rec.api_id, code=rec.code)
            if data:
                old_code = rec.api_status_code
                rec._apply_api_response(data)
                if rec.api_status_code != old_code:
                    rec.message_post(
                        body='Status updated: <b>%s</b>' % (rec.api_status_name or rec.api_status_code)
                    )

    # ── Webhook entry point ───────────────────────────────────────────────────

    @api.model
    def _process_webhook(self, payload):
        """
        Called by the webhook controller with the raw JSON payload.
        Extracts shipment code + status and triggers the delivery flow if needed.
        """
        # Support different payload shapes from Accurate Logistics
        shipment_data = payload.get('shipment') or payload
        code = shipment_data.get('code') or payload.get('code')
        status_obj = shipment_data.get('status') or {}
        if isinstance(status_obj, dict):
            status_code = status_obj.get('code') or payload.get('status', '')
            status_name = status_obj.get('name') or payload.get('statusName', '')
        else:
            status_code = str(status_obj)
            status_name = payload.get('statusName', '')

        if not code:
            _logger.warning('Accurate webhook: no shipment code in payload %s', payload)
            return {'error': 'No shipment code in payload'}

        shipment = self.search([('code', '=', code)], limit=1)
        if not shipment:
            _logger.warning('Accurate webhook: shipment not found for code %s', code)
            return {'error': 'Shipment not found: %s' % code}

        shipment.write({'api_status_code': status_code, 'api_status_name': status_name})
        shipment.message_post(
            body='Webhook: status → <b>%s</b> (%s)' % (status_name or status_code, status_code)
        )

        # Trigger COD invoice + payment on delivery
        if shipment.delivery_company_id and shipment.delivery_company_id._is_delivered_code(status_code):
            shipment._on_delivered()

        return {'success': True, 'code': code, 'status': status_code}

    # ── COD: invoice + payment on delivery ────────────────────────────────────

    def _on_delivered(self):
        """
        Auto-create customer invoice + COD payment when Accurate marks a
        shipment as delivered.  Posted to the Delivery Company's journal.
        """
        self.ensure_one()

        if self.invoice_id:
            _logger.info('Accurate: shipment %s already has invoice %s, skipping.', self.name, self.invoice_id.name)
            return

        sale = self.sale_id
        delivery_company = self.delivery_company_id
        if not delivery_company or not delivery_company.journal_id:
            _logger.warning('Accurate: no delivery company/journal on shipment %s.', self.name)
            return

        # ── 1. Create invoice ──────────────────────────────────────────────
        if sale and sale.invoice_status in ('to invoice', 'nothing'):
            invoices = sale._create_invoices()
        elif sale and sale.invoice_ids.filtered(lambda i: i.state == 'draft'):
            invoices = sale.invoice_ids.filtered(lambda i: i.state == 'draft')
        else:
            _logger.warning('Accurate: nothing to invoice for sale %s.', sale and sale.name)
            return

        for invoice in invoices:
            if invoice.state == 'draft':
                invoice.action_post()

            # ── 2. Register COD payment via the payment-register wizard ───
            # In Odoo 17+ the wizard is the only reliable way to create a
            # payment that's automatically reconciled with the invoice.
            if invoice.payment_state in ('paid', 'in_payment'):
                continue

            register_vals = {
                'payment_date': fields.Date.today(),
                'journal_id': delivery_company.journal_id.id,
                'amount': invoice.amount_residual,
                'communication': 'Accurate COD - %s' % (self.code or self.name),
            }
            # Filter to fields that actually exist on the wizard
            wizard_model = self.env['account.payment.register'].with_context(
                active_model='account.move',
                active_ids=invoice.ids,
            )
            register_vals = {k: v for k, v in register_vals.items() if k in wizard_model._fields}
            wizard = wizard_model.create(register_vals)
            action_result = wizard.action_create_payments()

            # The wizard creates and posts the payment, then auto-reconciles.
            # Fetch the created payment for our records.
            payment = self.env['account.payment'].search(
                [('move_id', 'in', invoice.matched_payment_ids.ids)] if hasattr(invoice, 'matched_payment_ids')
                else [('ref', '=', register_vals.get('communication'))],
                order='id desc', limit=1,
            )
            # Fallback: search by journal + amount + date if not found
            if not payment:
                payment = self.env['account.payment'].search([
                    ('journal_id', '=', delivery_company.journal_id.id),
                    ('amount', '=', register_vals['amount']),
                    ('partner_id', '=', invoice.partner_id.id),
                ], order='id desc', limit=1)

            self.write({
                'invoice_id': invoice.id,
                'payment_id': payment.id if payment else False,
            })
            self.message_post(
                body=(
                    'Delivered! Invoice <b>%s</b> created and COD payment of '
                    '<b>%.2f</b> posted to journal <b>%s</b>.'
                ) % (invoice.name, register_vals['amount'], delivery_company.journal_id.name)
            )
            if sale:
                sale.message_post(
                    body='Accurate Logistics: shipment <b>%s</b> delivered. Invoice and payment created automatically.' % (self.code or self.name)
                )

    # ── Other actions ─────────────────────────────────────────────────────────

    def action_open_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            raise UserError('No invoice linked to this shipment yet.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoice',
            'res_model': 'account.move',
            'res_id': self.invoice_id.id,
            'view_mode': 'form',
        }

    def action_open_tracking(self):
        self.ensure_one()
        if not self.tracking_url:
            raise UserError('No tracking URL available yet.')
        return {'type': 'ir.actions.act_url', 'url': self.tracking_url, 'target': 'new'}

    def action_calculate_fees(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Calculate Shipping Fees',
            'res_model': 'accurate.calculate.fees.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_shipment_id': self.id,
                'default_delivery_company_id': self.delivery_company_id.id,
                'default_recipient_zone_id': self.recipient_zone_id.id,
                'default_recipient_subzone_id': self.recipient_subzone_id.id,
                'default_service_id': self.service_id.id if self.service_id else False,
                'default_price': self.price,
                'default_weight': self.weight,
            },
        }

    def action_reset_to_draft(self):
        self.write({'state': 'draft', 'error_message': False})

    def action_unlink_test_invoice(self):
        """TEST ONLY: clear invoice_id + payment_id links so the shipment can
        be 'delivered' again. Does not delete the invoice/payment themselves
        — you can clean those up manually in Accounting if you want.
        """
        for rec in self:
            rec.write({
                'invoice_id': False,
                'payment_id': False,
                'api_status_code': 'SENT',
                'api_status_name': 'Sent',
            })
            rec.message_post(body='<b>Test reset:</b> invoice + payment links cleared.')
        return True

    def action_mark_delivered(self):
        """Manually trigger the 'delivered' flow (invoice + COD payment + reconcile)
        without waiting for the webhook. Useful for testing the full flow end-to-end.
        """
        for rec in self:
            if rec.state != 'sent':
                raise UserError(
                    'Shipment must be sent to Accurate Logistics first. '
                    'الشحنة يجب إرسالها إلى أكيوريت لوجيستكس أولاً.'
                )
            if rec.invoice_id:
                raise UserError(
                    'This shipment already has an invoice (%s). '
                    'هذه الشحنة لها فاتورة بالفعل.' % rec.invoice_id.name
                )
            # Mark status locally as delivered (so the UI shows it)
            rec.write({
                'api_status_code': 'DELIVERED',
                'api_status_name': 'Delivered (Manual Test)',
            })
            rec.message_post(
                body='<b>Manual Test:</b> Shipment marked as delivered. '
                     'Triggering invoice + COD payment flow…'
            )
            rec._on_delivered()
        return True

    def action_open_sale(self):
        self.ensure_one()
        if not self.sale_id:
            raise UserError('No sale order linked.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sale Order',
            'res_model': 'sale.order',
            'res_id': self.sale_id.id,
            'view_mode': 'form',
        }

    def action_open_picking(self):
        self.ensure_one()
        if not self.picking_id:
            raise UserError('No delivery order linked.')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Delivery Order',
            'res_model': 'stock.picking',
            'res_id': self.picking_id.id,
            'view_mode': 'form',
        }

    # ── Cron ──────────────────────────────────────────────────────────────────

    @api.model
    def cron_sync_statuses(self):
        pending = self.search([('state', '=', 'sent'), ('api_id', '!=', False)])
        _logger.info('Accurate Logistics cron: syncing %d shipments.', len(pending))
        for rec in pending:
            try:
                if not rec.delivery_company_id:
                    continue
                data = rec.delivery_company_id._al_get_shipment(api_id=rec.api_id, code=rec.code)
                if data:
                    old_code = rec.api_status_code
                    rec._apply_api_response(data)
                    if (
                        rec.api_status_code != old_code
                        and rec.delivery_company_id
                        and rec.delivery_company_id._is_delivered_code(rec.api_status_code)
                        and not rec.invoice_id
                    ):
                        rec._on_delivered()
            except Exception as exc:
                _logger.warning('Accurate cron: failed for %s: %s', rec.name, exc)
