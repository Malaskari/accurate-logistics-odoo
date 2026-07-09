import logging

from markupsafe import Markup

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
        [
            ('draft', 'Draft'),
            ('sent', 'Sent'),
            ('delivered', 'Delivered'),
            ('returned', 'Returned'),
            ('cancelled', 'Cancelled'),
            ('error', 'Error'),
        ],
        default='draft', required=True, copy=False, tracking=True, index=True,
    )

    # Track journal entries created by the shipping-fee booking so we can
    # reverse them if the shipment is later returned or cancelled.
    expense_move_id = fields.Many2one(
        'account.move',
        string='Shipping Expense Entry',
        readonly=True, copy=False,
    )
    cancellation_reason_id = fields.Many2one(
        'accurate.cancellation.reason',
        string='Cancellation Reason',
        readonly=True, copy=False,
    )
    cancellation_notes = fields.Text(
        'Cancellation Notes', readonly=True, copy=False,
    )
    api_status_code = fields.Char('API Status Code', readonly=True, copy=False)
    api_status_name = fields.Char('API Status', readonly=True, copy=False, tracking=True)
    tracking_url = fields.Char('Tracking URL', readonly=True, copy=False)
    error_message = fields.Text('Last Error', readonly=True, copy=False)

    # ── Delivery agent (courier's assigned driver) ────────────────────────────
    # Populated automatically from the Accurate API (lastDeliveryAgent) on every
    # status sync — assigned by the courier once the shipment is out for
    # delivery. Never entered by hand.
    agent_name = fields.Char('Delivery Agent', readonly=True, copy=False, tracking=True)
    agent_phone = fields.Char('Agent Phone', readonly=True, copy=False)
    agent_mobile = fields.Char('Agent Mobile', readonly=True, copy=False)
    agent_api_id = fields.Integer('Agent API ID', readonly=True, copy=False)
    agent_contact = fields.Char(
        'Agent Contact', compute='_compute_agent_contact', store=True,
        help='Best phone number to reach the delivery agent (mobile, else phone).',
    )

    @api.depends('agent_phone', 'agent_mobile')
    def _compute_agent_contact(self):
        for rec in self:
            rec.agent_contact = rec.agent_mobile or rec.agent_phone or ''

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
        domain="[('is_subzone', '=', True), ('parent_id', '=', recipient_zone_id), ('in_price_list', '=', True)] if recipient_zone_id else [('id', '=', 0)]",
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

    service_id = fields.Many2one(
        'accurate.service', string='Shipping Service', tracking=True,
        domain="[('company_id', '=', delivery_company_id)] if delivery_company_id else [('id', '=', 0)]",
    )

    @api.onchange('delivery_company_id')
    def _onchange_delivery_company_id(self):
        for s in self:
            company = s.delivery_company_id
            if s.service_id and s.service_id.company_id != company:
                s.service_id = False
            if s.recipient_zone_id and company not in s.recipient_zone_id.delivery_company_ids:
                s.recipient_zone_id = False
                s.recipient_subzone_id = False
            if s.sender_zone_id and company not in s.sender_zone_id.delivery_company_ids:
                s.sender_zone_id = False
                s.sender_subzone_id = False
            if company and company.default_service_id and not s.service_id:
                s.service_id = company.default_service_id
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

    def message_post(self, **kwargs):
        # Our programmatic chatter bodies are plain `str` containing HTML
        # (e.g. "Status → <b>RTS</b>"). Odoo 18 escapes a plain str body, so
        # the literal tags show up. Wrap in Markup so the HTML renders. UI
        # messages already arrive as sanitized HTML, so wrapping is harmless.
        body = kwargs.get('body')
        if isinstance(body, str) and not isinstance(body, Markup):
            kwargs['body'] = Markup(body)
        return super().message_post(**kwargs)

    # ── Sale Order status log ─────────────────────────────────────────────────
    #
    # The shipment chatter keeps the detailed technical timeline (every
    # API call, invoice posting, expense entry, etc.). The linked Sale
    # Order's chatter gets ONLY clean lifecycle events in Arabic so the
    # salesperson sees a high-level summary, not a noisy log.

    def _so_status_log(self, en_msg, ar_msg=None):
        """Post a clean log note on the linked Sale Order, in the current
        user's language (Arabic if `lang` starts with 'ar_', otherwise the
        English text).  Uses 'Log Note' subtype — internal only, no email
        to followers.  Quiet no-op if there's no linked SO.
        """
        self.ensure_one()
        sale = self.sale_id
        if not sale:
            return
        lang = self.env.user.lang or 'en_US'
        body = ar_msg if (ar_msg and lang.startswith('ar')) else en_msg
        try:
            sale.message_post(
                body=Markup(body),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        except Exception as exc:
            _logger.info(
                'Accurate: could not post SO status log on %s: %s',
                sale.name, exc,
            )

    # ── API dispatch ──────────────────────────────────────────────────────────

    def action_send_to_api(self):
        for rec in self:
            rec._send_to_api()

    @staticmethod
    def _al_clean_phone(value):
        """Normalise a phone number to the format Accurate's GraphQL API
        accepts (Libya: 10 digits starting with 09).

        Strips whitespace, dashes, parens, dots, and the international
        prefixes (+218, 218, 00218). Leaves a clean national number
        starting with 0 when possible.
        """
        if not value:
            return value
        s = str(value).strip()
        if not s:
            return s
        # Drop every char except digits (also drops + because the API rejects it).
        digits = ''.join(ch for ch in s if ch.isdigit())
        # Strip Libya country code variants.
        if digits.startswith('00218'):
            digits = digits[5:]
        elif digits.startswith('218'):
            digits = digits[3:]
        # Ensure a leading 0 on national numbers (mobile starts with 9).
        if digits and digits[0] != '0':
            digits = '0' + digits
        return digits or value

    def _send_to_api(self):
        self.ensure_one()

        if not (self.recipient_zone_id.api_id and self.recipient_subzone_id.api_id):
            raise UserError(
                'Recipient zone and sub-zone must have valid API IDs. '
                'Go to Accurate Logistics → Configuration → Zones and sync them first.'
            )

        # Resolve a shipping service — required by the API. Always within
        # the same Delivery Company so we don't pick a service belonging to
        # a different merchant account.
        service = self.service_id
        if not service and self.delivery_company_id:
            service = self.delivery_company_id.default_service_id
        if not service and self.delivery_company_id:
            service = self.env['accurate.service'].search([
                ('api_id', '!=', False),
                ('company_id', '=', self.delivery_company_id.id),
            ], limit=1)
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
            'recipientMobile': self._al_clean_phone(self.recipient_mobile),
            'recipientPhone': self._al_clean_phone(self.recipient_phone),
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
        _set('senderPhone', self._al_clean_phone(self.sender_phone))
        _set('senderMobile', self._al_clean_phone(self.sender_mobile))
        _set('senderAddress', self.sender_address)
        _set('senderPostalCode', self.sender_postal_code)
        if self.sender_zone_id and self.sender_zone_id.api_id:
            inp['senderZoneId'] = self.sender_zone_id.api_id
        if self.sender_subzone_id and self.sender_subzone_id.api_id:
            inp['senderSubzoneId'] = self.sender_subzone_id.api_id
        _set('piecesCount', self.pieces_count)
        _set('returnPiecesCount', self.return_pieces_count)
        # price is REQUIRED by the API — always send it, even when 0 (the
        # _set helper would otherwise drop a 0 value as "empty").
        inp['price'] = self.price or 0.0
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
        company_name = self.delivery_company_id.name if self.delivery_company_id else ''
        self._so_status_log(
            en_msg='🚚 New shipment created in <b>%s</b> for this order — code: <b>%s</b>'
                   % (company_name, self.code or '—'),
            ar_msg='🚚 تم إنشاء شحنة جديدة في نظام <b>%s</b> لهذا الطلب برقم <b>%s</b>'
                   % (company_name, self.code or '—'),
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
            scode = data['status'].get('code') or ''
            sid = data['status'].get('id') or ''
            # Some tenants omit status.code (only id + name). Fall back to the
            # id so api_status_code is never blank — this also makes the
            # "did the status change?" check in the cron / bulk-sync work.
            vals['api_status_code'] = scode or (str(sid) if sid else '')
            vals['api_status_name'] = data['status'].get('name', '')
        for src, dst in [
            ('amount', 'fee_amount'),
            ('deliveryFees', 'fee_delivery'),
            ('collectionFees', 'fee_collection'),
            ('totalAmount', 'fee_total'),
        ]:
            if data.get(src) is not None:
                vals[dst] = data[src]
        # Capture the cancellation / failed-delivery / return reason. The full
        # API record carries it as cancellationReason {id, name} even for a
        # failed-delivery (DEX) — the webhook itself usually doesn't.
        reason = data.get('cancellationReason')
        if isinstance(reason, dict) and (reason.get('id') or reason.get('name')):
            rname = reason.get('name') or ''
            rid = reason.get('id')
            match = False
            if rid:
                match = self.env['accurate.cancellation.reason'].search(
                    [('api_id', '=', rid)], limit=1,
                )
            if match:
                vals['cancellation_reason_id'] = match.id
            elif rname:
                vals['cancellation_notes'] = rname
        # Delivery agent (courier's assigned driver) — see _al_agent_vals.
        agent_vals, agent_changed = self._al_agent_vals(data)
        vals.update(agent_vals)
        if vals:
            self.write(vals)
        if agent_changed:
            self._al_post_agent_note(agent_vals)

    def _al_agent_vals(self, data):
        """Extract delivery-agent field values from an API shipment payload
        (``lastDeliveryAgent``). Returns ``(vals, changed)``.

        Returns an empty dict when the payload carries no agent, so a webhook /
        response that omits it never wipes a previously-synced agent. The API id
        is coerced to int so the change check compares like-for-like against the
        stored Integer field (a string id would otherwise flag a change on every
        sync and spam the chatter)."""
        self.ensure_one()
        agent = data.get('lastDeliveryAgent')
        if not (isinstance(agent, dict) and (agent.get('id') or agent.get('name'))):
            return {}, False
        try:
            new_aid = int(agent.get('id') or 0)
        except (TypeError, ValueError):
            new_aid = 0
        new_name = agent.get('name') or ''
        changed = new_aid != self.agent_api_id or new_name != (self.agent_name or '')
        return {
            'agent_api_id': new_aid,
            'agent_name': new_name,
            'agent_phone': agent.get('phone') or '',
            'agent_mobile': agent.get('mobile') or '',
        }, changed

    def _al_post_agent_note(self, agent_vals):
        contact = agent_vals.get('agent_mobile') or agent_vals.get('agent_phone') or ''
        name = agent_vals.get('agent_name') or '—'
        suffix = (' — ' + contact) if contact else ''
        # Detailed timeline on the shipment…
        self.message_post(body='🛵 Delivery agent: <b>%s</b>%s' % (name, suffix))
        # …and a clean lifecycle note on the linked Sale Order chatter so the
        # salesperson sees who is delivering (Arabic for ar_ users).
        self._so_status_log(
            en_msg='🛵 Delivery agent assigned: <b>%s</b>%s' % (name, suffix),
            ar_msg='🛵 تم تعيين مندوب التوصيل: <b>%s</b>%s' % (name, suffix),
        )

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

    def action_sync_status_bulk(self):
        """Bulk status sync for the SELECTED shipments (list-view header
        button). Unlike action_sync_status it never raises mid-batch:
        records that aren't ready are skipped, per-record API errors are
        caught, and when a status actually changes it fires the same
        delivered / returned / cancelled flows as the cron — so the user
        can manually catch up shipments the webhook may have missed.

        Shows a summary notification at the end.
        """
        synced = changed = skipped = errored = 0
        for rec in self:
            # Skip records not sent yet or already in a terminal state.
            if (not rec.api_id and not rec.code) or not rec.delivery_company_id:
                skipped += 1
                continue
            if rec.state in ('returned', 'cancelled'):
                skipped += 1
                continue
            try:
                data = rec.delivery_company_id._al_get_shipment(
                    api_id=rec.api_id, code=rec.code,
                )
                if not data:
                    skipped += 1
                    continue
                old_code = rec.api_status_code
                rec._apply_api_response(data)
                synced += 1
                # State-based dispatch (NOT change-based): fire the flow
                # whenever the current status maps to a family and the
                # shipment isn't already in that terminal state. The _on_*
                # handlers are idempotent (they early-return if already
                # processed), so this safely catches up shipments whose
                # status was synced before the flow logic existed.
                company = rec.delivery_company_id
                code, name = rec.api_status_code, rec.api_status_name
                fired = False
                # _on_delivered is idempotent — call it even if already
                # delivered so a stuck outgoing picking gets validated.
                if company._is_delivered_code(code, name):
                    rec._on_delivered()
                    fired = rec.state != 'delivered' or not rec.invoice_id
                elif company._is_returned_code(code, name) and rec.state != 'returned':
                    rec._on_returned(); fired = True
                elif company._is_cancelled_code(code, name) and rec.state != 'cancelled':
                    rec._on_cancelled(); fired = True
                elif rec.api_status_code != old_code:
                    rec.message_post(
                        body='Status updated: <b>%s</b>'
                             % (rec.api_status_name or rec.api_status_code)
                    )
                if fired:
                    changed += 1
            except Exception as exc:
                errored += 1
                _logger.warning(
                    'Accurate bulk sync failed for %s: %s', rec.name, exc,
                )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync Complete / اكتملت المزامنة',
                'message': (
                    'Synced %d (status changed: %d), skipped %d, errors %d.'
                    % (synced, changed, skipped, errored)
                ),
                'type': 'warning' if errored else 'success',
                'sticky': bool(errored),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    # ── Webhook entry point ───────────────────────────────────────────────────

    @api.model
    def _webhook_secret_valid(self, received, payload):
        """Validate the incoming webhook secret. Accepts when `received`
        matches:
          - the secret of the Delivery Company that owns the shipment in the
            payload (strict, preferred), OR
          - the global secret (ir.config_parameter), as fallback, OR
          - any configured company secret (when the shipment isn't found yet).
        If NO secret is configured anywhere → open mode (allow), preserving
        the original behaviour for un-configured installs.
        """
        Param = self.env['ir.config_parameter'].sudo()
        global_secret = Param.get_param('accurate_logistics.webhook_secret', '') or ''

        companies = self.env['accurate.delivery.company'].sudo().search([
            ('webhook_secret', '!=', False),
        ])
        company_secrets = {c.webhook_secret for c in companies if c.webhook_secret}

        # Nothing configured anywhere → don't block (open mode).
        if not global_secret and not company_secrets:
            return True
        if not received:
            return False

        # Try to scope strictly to the shipment's own company.
        data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
        shipment_data = {}
        code = None
        if isinstance(data, dict):
            shipment_data = data.get('shipment') if isinstance(data.get('shipment'), dict) else data
            code = (shipment_data or {}).get('code') or data.get('code')
        if code:
            shipment = self.search([('code', '=', code)], limit=1)
            company = shipment.delivery_company_id if shipment else False
            if company and company.webhook_secret:
                return received in (company.webhook_secret, global_secret)

        # Shipment not found / its company has no secret → accept any valid one.
        return received == global_secret or received in company_secrets

    @api.model
    def _process_webhook(self, payload):
        """
        Called by the webhook controller with the raw JSON payload.
        Extracts shipment code + status and triggers the delivery flow if needed.
        """
        # ── Parse the payload ─────────────────────────────────────────────────
        # The REAL Accurate webhook is FLAT and uses its own field names:
        #   { "shipmentId": 515572, "shipmentCode": "Y034051",
        #     "typeCode": "SHP_STATUS_UPDATE",
        #     "shipmentStatusCode": "RITS",          ← the status CODE
        #     "cancellationReasonId": null, "returnTypeCode": null,
        #     "deliveredAmount": "0", "notes": null }
        # We also keep back-compat for the GraphQL-style nested shape
        #   { "data": { "shipment": { "code": ..., "status": {id,code,name} } } }
        if isinstance(payload.get('data'), dict):
            payload = payload['data']
        shipment_data = payload.get('shipment') if isinstance(payload.get('shipment'), dict) else payload

        code = (
            payload.get('shipmentCode')
            or shipment_data.get('shipmentCode')
            or shipment_data.get('code')
            or payload.get('code')
        )

        # Status code: webhook = shipmentStatusCode; GraphQL = status.code / name / id
        status_obj = shipment_data.get('status') if isinstance(shipment_data.get('status'), dict) else {}
        status_code = (
            payload.get('shipmentStatusCode')
            or shipment_data.get('shipmentStatusCode')
            or status_obj.get('code')
            or ''
        )
        status_name = status_obj.get('name') or payload.get('statusName') or ''
        status_id = status_obj.get('id') or ''

        if not code:
            _logger.warning('Accurate webhook: no shipment code in payload %s', payload)
            return {'error': 'No shipment code in payload'}

        shipment = self.search([('code', '=', code)], limit=1)
        if not shipment:
            _logger.warning('Accurate webhook: shipment not found for code %s', code)
            return {'error': 'Shipment not found: %s' % code}

        company = shipment.delivery_company_id

        # The webhook only carries the status CODE, not the Arabic name.
        # Best-effort: pull the full record from the API to get the friendly
        # name + latest fees. Keep the webhook's code as authoritative for
        # matching (the GraphQL response often omits status.code).
        refreshed_reason = None  # cancellationReason {id, name} from the API
        if not status_name and company and (shipment.api_id or shipment.code):
            try:
                data = company._al_get_shipment(api_id=shipment.api_id, code=shipment.code)
                if data:
                    st = data.get('status') or {}
                    status_name = st.get('name') or status_name
                    if not status_code:
                        status_code = st.get('code') or (str(st.get('id')) if st.get('id') else '')
                    for src, dst in [
                        ('amount', 'fee_amount'),
                        ('deliveryFees', 'fee_delivery'),
                        ('collectionFees', 'fee_collection'),
                        ('totalAmount', 'fee_total'),
                    ]:
                        if data.get(src) is not None:
                            shipment[dst] = data[src]
                    # The full record now also carries lastDeliveryAgent — capture
                    # the courier's assigned driver on the webhook path too (this
                    # refresh runs for the common flat webhook that has no status
                    # name), not only on manual / bulk / cron sync.
                    agent_vals, agent_changed = shipment._al_agent_vals(data)
                    if agent_vals:
                        shipment.write(agent_vals)
                        if agent_changed:
                            shipment._al_post_agent_note(agent_vals)
                    # The full record carries the reason even for failed
                    # deliveries (DEX) where the webhook sends none.
                    if isinstance(data.get('cancellationReason'), dict):
                        refreshed_reason = data['cancellationReason']
            except Exception as exc:
                _logger.info('Accurate webhook: refresh fetch failed for %s: %s', code, exc)

        stored_code = status_code or (str(status_id) if status_id else '')
        shipment.write({
            'api_status_code': stored_code,
            'api_status_name': status_name or stored_code,
        })
        shipment.message_post(
            body='Webhook: status → <b>%s</b> (%s)'
                 % (status_name or stored_code, stored_code or '—')
        )

        # ── Capture cancellation / failed-delivery / return reason ────────────
        vals = {}
        # 1) Webhook: cancellationReasonId (an id) → look up the synced reason.
        reason_id = payload.get('cancellationReasonId') or shipment_data.get('cancellationReasonId')
        # 2) API refresh: cancellationReason {id, name} (covers DEX etc.).
        if not reason_id and refreshed_reason:
            reason_id = refreshed_reason.get('id')
        if reason_id:
            reason = self.env['accurate.cancellation.reason'].search(
                [('api_id', '=', reason_id)], limit=1,
            )
            if reason:
                vals['cancellation_reason_id'] = reason.id
            elif refreshed_reason and refreshed_reason.get('name'):
                vals['cancellation_notes'] = refreshed_reason['name']
        # Free-text notes (webhook 'notes', or nested shapes).
        notes = payload.get('notes') or shipment_data.get('notes')
        if notes:
            vals['cancellation_notes'] = notes
        # Back-compat: nested cancellationReason {name} shape.
        if not vals.get('cancellation_reason_id'):
            reason_label, reason_notes = self._extract_webhook_reason(payload, shipment_data)
            if reason_notes and not vals.get('cancellation_notes'):
                vals['cancellation_notes'] = reason_notes
            if reason_label:
                Reason = self.env['accurate.cancellation.reason']
                domain = ['|', ('name', '=', reason_label), ('code', '=', reason_label)]
                if company:
                    domain = ['&', ('company_id', '=', company.id)] + domain
                match = Reason.search(domain, limit=1)
                if match:
                    vals['cancellation_reason_id'] = match.id
                elif not vals.get('cancellation_notes'):
                    vals['cancellation_notes'] = reason_label
        if vals:
            shipment.write(vals)

        # ── Dispatch (state-based; match on code OR name OR id) ───────────────
        # _on_delivered is idempotent (re-validates pending pickings, invoices
        # only once) so we call it even if already delivered — this catches up
        # an outgoing picking that was still pending at the first DTR event.
        if company:
            if company._is_delivered_code(stored_code, status_name, status_id):
                shipment._on_delivered()
            elif company._is_returned_code(stored_code, status_name, status_id) \
                    and shipment.state != 'returned':
                shipment._on_returned()
            elif company._is_cancelled_code(stored_code, status_name, status_id) \
                    and shipment.state != 'cancelled':
                shipment._on_cancelled()

        return {'success': True, 'code': code, 'status': stored_code}

    @staticmethod
    def _extract_webhook_reason(payload, shipment_data):
        """Dig a human-readable reason label + free-text notes out of the
        webhook payload. Accurate (and its tenants) send this under several
        possible keys and shapes:

          - payload['cancellationReason'] / ['returnReason'] / ['reason']
            → either a plain string, or a dict {id, code, name}
          - payload['notes'] / ['note'] / ['comment'] / ['remark']
            → free-text notes

        Returns (label, notes) — either may be '' / None.
        """
        def _dig(*keys):
            for src in (shipment_data or {}, payload or {}):
                for k in keys:
                    if isinstance(src, dict) and src.get(k):
                        return src[k]
            return None

        raw_reason = _dig(
            'cancellationReason', 'returnReason', 'rejectReason',
            'reason', 'statusReason',
        )
        label = ''
        if isinstance(raw_reason, dict):
            label = (raw_reason.get('name')
                     or raw_reason.get('label')
                     or raw_reason.get('code')
                     or '')
        elif raw_reason:
            label = str(raw_reason)

        notes = _dig('notes', 'note', 'comment', 'remark', 'description') or ''
        if notes and not isinstance(notes, str):
            notes = str(notes)

        return label, notes

    # ── COD: invoice + payment on delivery ────────────────────────────────────

    def _on_delivered(self):
        """
        Auto-create customer invoice + COD payment when Accurate marks a
        shipment as delivered.  Posted to the Delivery Company's journal.
        """
        self.ensure_one()

        # Mark as delivered + log only the FIRST time (idempotent re-runs).
        if self.state != 'delivered':
            self.state = 'delivered'
            self._so_status_log(
                en_msg='✅ Shipment <b>%s</b> delivered to the customer.'
                       % (self.code or self.name),
                ar_msg='✅ تم تسليم الشحنة <b>%s</b> إلى العميل.'
                       % (self.code or self.name),
            )

        # ALWAYS attempt to validate the outgoing picking — even on re-runs.
        # This catches the case where the internal Pick/Pack step was
        # completed AFTER the delivery event (the guardrail blocked it the
        # first time, then the warehouse finished the internal step later).
        try:
            self._validate_delivery_pickings()
        except Exception as exc:
            _logger.warning(
                'Accurate: auto-validate pickings failed for %s: %s',
                self.name, exc,
            )

        # Invoice already created on a previous run → nothing more to do.
        if self.invoice_id:
            return

        sale = self.sale_id
        delivery_company = self.delivery_company_id
        if not delivery_company or not delivery_company.journal_id:
            _logger.warning('Accurate: no delivery company/journal on shipment %s.', self.name)
            return

        # ── 1. Create invoice ──────────────────────────────────────────────
        # Wrap _create_invoices in try/except: Odoo raises a UserError when
        # the product uses "Delivered Quantities" policy and qty_delivered=0
        # (pickings not validated yet). We log + bail silently instead of
        # blowing up the whole flow with a modal popup.
        invoices = self.env['account.move']
        if sale and sale.invoice_status in ('to invoice', 'nothing'):
            try:
                invoices = sale._create_invoices()
            except Exception as exc:
                _logger.info(
                    'Accurate: cannot auto-create invoice for %s yet (%s) — '
                    'validate the delivery pickings, then click Mark Delivered '
                    'again.', sale.name, exc,
                )
                return
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
            # Posted via message_post → auto-mirrored to the SO chatter.
            self.message_post(
                body=(
                    'Delivered! Invoice <b>%s</b> created and COD payment of '
                    '<b>%.2f</b> posted to journal <b>%s</b>.'
                ) % (invoice.name, register_vals['amount'], delivery_company.journal_id.name)
            )

            # ── 4. Book courier's delivery fee as an expense ──────────────
            # Only when the shipment uses "Shipping Fee Included in Price".
            # In that mode the customer paid the full amount (incl. shipping),
            # the courier collected it all and remits (collection - fee), so we
            # record the difference as a shipping expense.
            if self.price_type_code == 'INCLD':
                self._book_shipping_fee_expense()

    def _validate_delivery_pickings(self):
        """When the shipment is marked delivered, auto-validate ONLY the
        outgoing picking (the one going to Partners/Customers).

        Returns True if the outgoing picking was validated (or there was
        nothing to validate), False if the guardrail blocked it because
        internal Pick / Pack steps are still pending.

        Guardrail: if any internal Pick / Pack step is still NOT done, do
        NOT auto-validate the outgoing — instead, log an activity on the
        outgoing picking pinging the users listed on the Delivery Company's
        "Users to Notify" field so the warehouse reconciles first.

        Internal Pick / Pack steps stay manual — the warehouse validates
        them when they hand the package to the courier. The outgoing step
        represents the courier-to-customer leg, which is what Accurate's
        DELIVERED status actually means.
        """
        self.ensure_one()
        pickings = self.env['stock.picking']
        if self.sale_id:
            pickings |= self.sale_id.picking_ids
        if self.picking_id:
            pickings |= self.picking_id

        # The outgoing picking to the customer.
        outgoing = pickings.filtered(
            lambda p: p.picking_type_code == 'outgoing'
            and p.state in ('confirmed', 'waiting', 'assigned')
        )
        if not outgoing:
            # Nothing to validate — already done or no outgoing picking. We
            # return True so the caller proceeds with invoice creation
            # (Odoo will refuse if qty_delivered is still 0).
            return True

        # Guardrail: are there any internal steps still pending?
        internal_pending = pickings.filtered(
            lambda p: p.picking_type_code != 'outgoing'
            and p.state not in ('done', 'cancel')
        )
        if internal_pending:
            self._notify_internal_pick_pending(outgoing, internal_pending)
            return False

        validated_names = []
        for pick in outgoing:
            # Reserve stock if it isn't already.
            try:
                if pick.state in ('confirmed', 'waiting') and hasattr(pick, 'action_assign'):
                    pick.action_assign()
            except Exception:
                pass

            # Make sure every move has its done quantity filled — otherwise
            # button_validate opens an immediate-transfer / detailed-ops
            # wizard. Field name varies by Odoo version.
            for move in pick.move_ids:
                if move.state in ('done', 'cancel'):
                    continue
                demand = move.product_uom_qty or 0.0
                if not demand:
                    continue
                for fname in ('quantity', 'quantity_done'):
                    if fname in move._fields:
                        try:
                            setattr(move, fname, demand)
                        except Exception:
                            pass
                        break

            try:
                ctx = {'skip_backorder': True, 'skip_sms': True,
                       'cancel_backorder': True, 'skip_immediate': True}
                res = pick.with_context(**ctx).button_validate()
                # Some Odoo versions return a wizard action when there's a
                # backorder candidate — auto-confirm "no backorder".
                if isinstance(res, dict) and res.get('res_model') in (
                    'stock.backorder.confirmation',
                    'stock.immediate.transfer',
                ):
                    wctx = res.get('context', {}) or {}
                    Wiz = self.env[res['res_model']].with_context(**wctx)
                    wiz = Wiz.create({})
                    if hasattr(wiz, 'process_cancel_backorder'):
                        wiz.process_cancel_backorder()
                    elif hasattr(wiz, 'process'):
                        wiz.process()
                validated_names.append(pick.name)
            except Exception as exc:
                _logger.warning(
                    'Accurate: failed to auto-validate picking %s: %s',
                    pick.name, exc,
                )
                self._chatter(
                    '<b>Warning:</b> Could not auto-validate picking '
                    '<b>%s</b> on delivery: %s' % (pick.name, exc)
                )
        if validated_names:
            self._chatter(
                'Auto-validated picking(s) on delivery: <b>%s</b>.'
                % ', '.join(validated_names)
            )
        return True

    def _notify_internal_pick_pending(self, outgoing_pickings, internal_pending):
        """Courier says DELIVERED but warehouse hasn't finished internal
        Pick/Pack. Don't auto-validate — instead, log an activity on each
        outgoing picking targeting the users on Delivery Company's
        notify_user_ids list, and post a chatter trail.
        """
        self.ensure_one()
        company = self.delivery_company_id
        users = company.notify_user_ids if company else self.env['res.users']
        # Fallback: at least notify the salesperson on the SO + the shipment's
        # creator, otherwise the activity has no assignee.
        if not users:
            fallback = self.env['res.users']
            if self.sale_id and self.sale_id.user_id:
                fallback |= self.sale_id.user_id
            if self.create_uid:
                fallback |= self.create_uid
            users = fallback

        pending_names = ', '.join(internal_pending.mapped('name'))
        summary = 'Accurate: Delivered but internal pick not done'
        note = (
            'Courier marked shipment <b>%s</b> as <b>DELIVERED</b> but the '
            'internal warehouse step(s) <b>%s</b> are not validated yet. '
            'The outgoing picking was NOT auto-validated. Please reconcile '
            'the warehouse first, then validate the outgoing picking '
            'manually.<br/><br/>'
            'تم تسليم الشحنة <b>%s</b> من قبل المندوب لكن خطوة الإخراج '
            'الداخلية <b>%s</b> غير مكتملة. الرجاء إنهاؤها يدويًا أولاً.'
        ) % (
            self.code or self.name, pending_names,
            self.code or self.name, pending_names,
        )

        activity_type = self.env.ref('mail.mail_activity_data_warning',
                                     raise_if_not_found=False) \
            or self.env.ref('mail.mail_activity_data_todo',
                            raise_if_not_found=False)
        Activity = self.env['mail.activity']
        Picking = self.env['stock.picking']
        picking_model_id = self.env['ir.model']._get_id('stock.picking')

        notified_logins = []
        for pick in outgoing_pickings:
            for user in users:
                try:
                    Activity.create({
                        'res_model_id': picking_model_id,
                        'res_model': 'stock.picking',
                        'res_id': pick.id,
                        'activity_type_id': activity_type.id if activity_type else False,
                        'summary': summary,
                        'note': note,
                        'user_id': user.id,
                        'date_deadline': fields.Date.today(),
                    })
                    if user.login not in notified_logins:
                        notified_logins.append(user.login)
                except Exception as exc:
                    _logger.warning(
                        'Accurate: failed to create activity on %s for %s: %s',
                        pick.name, user.login, exc,
                    )
            # Also drop a chatter note on the picking itself so it's visible
            # even if the activity list is collapsed.
            try:
                pick.message_post(
                    body=note,
                    message_type='comment',
                    subtype_xmlid='mail.mt_comment',
                )
            except Exception:
                pass

        self._chatter(
            'Outgoing picking <b>%s</b> NOT auto-validated — internal step(s) '
            '<b>%s</b> still pending. Activity logged for: <b>%s</b>.'
            % (
                ', '.join(outgoing_pickings.mapped('name')),
                pending_names,
                ', '.join(notified_logins) if notified_logins else '(no users configured)',
            )
        )
        self._so_status_log(
            en_msg='⚠️ Shipment <b>%s</b> reported delivered by the courier, '
                   'but the internal warehouse step is still not validated — '
                   'manual confirmation required.'
                   % (self.code or self.name),
            ar_msg='⚠️ الشحنة <b>%s</b> مسلَّمة من المندوب لكن خطوة التجهيز '
                   'الداخلية لم تكتمل بعد — يلزم تأكيدها يدويًا.'
                   % (self.code or self.name),
        )

    def _book_shipping_fee_expense(self):
        """Post a journal entry that records the courier's delivery fee as
        an expense and reduces the COD journal's cash/bank balance by the
        same amount.

        Dr. Shipping Expense  fee
            Cr. COD Journal Bank/Cash  fee

        This represents what the courier kept from the COD collection.
        Triggered only when price_type_code == 'INCLD' and we have a
        non-zero fee.
        """
        self.ensure_one()
        company = self.delivery_company_id
        if not company:
            return
        if not company.expense_account_id:
            _logger.info(
                'Accurate: shipment %s is INCLD but no Shipping Expense '
                'Account configured on Delivery Company %s — skipping.',
                self.name, company.name,
            )
            self.message_post(
                body='<b>Note:</b> Shipping fee not booked — Delivery Company '
                     '<b>%s</b> has no Shipping Expense Account set.' % company.name
            )
            return
        if not company.journal_id:
            return

        fee = self.fee_delivery or self.fee_total or 0.0
        if fee <= 0:
            _logger.info(
                'Accurate: shipment %s INCLD but fee is 0 — skipping expense booking.',
                self.name,
            )
            return

        # Resolve the journal's cash/bank account.
        journal = company.journal_id
        cash_account = (
            journal.default_account_id
            or getattr(journal, 'payment_credit_account_id', False)
            or getattr(journal, 'payment_debit_account_id', False)
        )
        if not cash_account:
            _logger.warning(
                'Accurate: cannot book shipping expense for %s — journal %s has '
                'no default account.',
                self.name, journal.name,
            )
            self.message_post(
                body='<b>Warning:</b> Shipping fee not booked — Journal '
                     '<b>%s</b> has no default account configured.' % journal.name
            )
            return

        currency = journal.currency_id or self.env.company.currency_id
        ref = 'Accurate shipping fee — %s' % (self.code or self.name)
        line_name = 'Shipping fee retained by %s — %s' % (
            company.name, self.code or self.name
        )

        move = self.env['account.move'].create({
            'journal_id': journal.id,
            'date': fields.Date.today(),
            'ref': ref,
            'move_type': 'entry',
            'line_ids': [
                (0, 0, {
                    'name': line_name,
                    'account_id': company.expense_account_id.id,
                    'debit': fee,
                    'credit': 0.0,
                    'currency_id': currency.id,
                }),
                (0, 0, {
                    'name': line_name,
                    'account_id': cash_account.id,
                    'debit': 0.0,
                    'credit': fee,
                    'currency_id': currency.id,
                }),
            ],
        })
        try:
            move.action_post()
        except Exception as exc:
            _logger.warning(
                'Accurate: shipping-expense entry %s posted failed for %s: %s',
                move.name, self.name, exc,
            )
        # Save the move so we can reverse it later if the shipment is returned
        self.expense_move_id = move.id
        self.message_post(
            body=(
                'Shipping fee <b>%.2f %s</b> booked as expense to '
                '<b>%s</b> (entry <b>%s</b>) — courier retained from COD.'
            ) % (fee, currency.symbol or currency.name,
                 company.expense_account_id.display_name, move.name)
        )

    # ── Returned: reverse COD invoice + shipping expense ──────────────────────

    def _on_returned(self):
        """Webhook/cron handler when the shipment is returned to sender (RTRN).
        - Set state = 'returned' and refresh api_status to reflect it.
        - Reverse invoice + shipping-fee expense.
        - Cancel pending pickings + create a return picking if delivery was already validated.
        - Cancel the Sale Order so it disappears from the active pipeline.
        - Notify the sale order's chatter.
        """
        self.ensure_one()
        if self.state == 'returned':
            return
        self.write({
            'state': 'returned',
            'api_status_code': 'RTRN',
            'api_status_name': 'Returned',
        })
        self._chatter(
            '<b>Returned</b> — shipment was returned to sender. '
            'Reversing COD invoice / payment / shipping-expense, '
            'handling pickings, and cancelling the Sale Order.'
        )
        # Build the return reason from the reason record + notes captured by
        # the webhook, falling back to the courier's status name.
        reason_parts = []
        if self.cancellation_reason_id:
            reason_parts.append(
                self.cancellation_reason_id.name
                or self.cancellation_reason_id.code
                or ''
            )
        if self.cancellation_notes:
            reason_parts.append(self.cancellation_notes)
        if not reason_parts:
            reason_parts.append(self.api_status_name or 'Returned by courier')
        return_reason = ' — '.join(p for p in reason_parts if p) or '—'
        self._so_status_log(
            en_msg='↩️ Shipment <b>%s</b> returned. Reason: <b>%s</b>.'
                   % (self.code or self.name, return_reason),
            ar_msg='↩️ تم إرجاع الشحنة <b>%s</b>. السبب: <b>%s</b>.'
                   % (self.code or self.name, return_reason),
        )

        reason = 'Shipment returned to sender'
        self._reverse_invoice_if_any(reason=reason)
        self._reverse_expense_if_any(reason=reason)

        # Snapshot the pickings BEFORE we touch the SO — once the SO is
        # cancelled, sale_id.picking_ids may still resolve them but their
        # state may have shifted, so we lock in the recordset here.
        captured = self.env['stock.picking']
        if self.sale_id:
            captured |= self.sale_id.picking_ids
        if self.picking_id:
            captured |= self.picking_id

        company = self.delivery_company_id
        if company and getattr(company, 'auto_cancel_pickings', True):
            self._cancel_pending_pickings(captured)

        # Create returns FIRST while the procurement group is still healthy,
        # so the new return picking inherits group_id (and therefore stays
        # linked to the Sale Order's Transfers list).
        created_returns = self.env['stock.picking']
        if company and getattr(company, 'auto_create_return_picking', True):
            created_returns = self._create_return_for_done_pickings(captured, reason)

        # Then cancel the SO. Skip if we were CALLED from the SO cancel
        # hook (avoids recursion).
        if not self.env.context.get('accurate_skip_so_cancel'):
            self._cancel_sale_order_if_any(reason=reason)

        # Revive any returns the SO cancel cascade killed. They share the
        # procurement group with the cancelled SO, so Odoo's group-level
        # cancel cascades to them — we explicitly reset both picking AND
        # its moves back to draft, then confirm+assign so the warehouse
        # can receive the goods.
        for ret in created_returns:
            ret.invalidate_recordset()
            if ret.state == 'cancel':
                try:
                    # Reset cancelled moves back to draft so action_confirm
                    # has something to work with. Bypass any tracking guards
                    # by writing the state directly.
                    for mv in ret.move_ids:
                        if mv.state == 'cancel':
                            mv.write({'state': 'draft'})
                    ret.write({'state': 'draft'})
                    ret.action_confirm()
                    try:
                        ret.action_assign()
                    except Exception:
                        # If nothing to reserve (e.g. no stock yet) the picking
                        # stays in 'confirmed' — that's fine, warehouse can
                        # validate it when goods arrive physically.
                        pass
                    self._chatter(
                        'Return picking <b>%s</b> revived after SO cancel '
                        'cascade — Ready to validate.' % ret.name
                    )
                except Exception as exc:
                    _logger.warning(
                        'Accurate: could not revive return %s after SO '
                        'cancel: %s', ret.name, exc,
                    )

    # ── Cancelled: reverse anything that was booked ───────────────────────────

    def _on_cancelled(self):
        """Webhook/cron/manual handler when the shipment is cancelled (RJCT
        or CANCELLED in the API).
        - Set state = 'cancelled' and refresh api_status to reflect it.
        - Reverse invoice + shipping-fee expense.
        - Cancel pending pickings + create a return picking if delivery was already validated.
        - Cancel the Sale Order so it disappears from the active pipeline.
        """
        self.ensure_one()
        if self.state == 'cancelled':
            return
        self.write({
            'state': 'cancelled',
            'api_status_code': 'CANCELLED',
            'api_status_name': 'Cancelled',
        })
        self._chatter(
            '<b>Cancelled</b> — shipment was cancelled. '
            'Reversing COD invoice / payment / shipping-expense, '
            'handling pickings, and cancelling the Sale Order.'
        )
        # Compose a reason line from cancellation_reason_id + notes (set
        # by the cancel wizard) — if neither is filled, fall back to the
        # courier's status label.
        reason_parts = []
        if self.cancellation_reason_id:
            reason_parts.append(
                self.cancellation_reason_id.name
                or self.cancellation_reason_id.code
                or ''
            )
        if self.cancellation_notes:
            reason_parts.append(self.cancellation_notes)
        if not reason_parts and self.api_status_name:
            reason_parts.append(self.api_status_name)
        reason_text = ' — '.join(p for p in reason_parts if p) or '—'
        self._so_status_log(
            en_msg='❌ Shipment <b>%s</b> cancelled. Reason: <b>%s</b>.'
                   % (self.code or self.name, reason_text),
            ar_msg='❌ تم إلغاء الشحنة <b>%s</b>. السبب: <b>%s</b>.'
                   % (self.code or self.name, reason_text),
        )

        reason = 'Shipment cancelled'
        self._reverse_invoice_if_any(reason=reason)
        self._reverse_expense_if_any(reason=reason)

        # Snapshot the pickings BEFORE we touch the SO.
        captured = self.env['stock.picking']
        if self.sale_id:
            captured |= self.sale_id.picking_ids
        if self.picking_id:
            captured |= self.picking_id

        company = self.delivery_company_id
        if company and getattr(company, 'auto_cancel_pickings', True):
            self._cancel_pending_pickings(captured)

        # Create returns FIRST while the procurement group is still healthy,
        # so the new return picking inherits group_id (and therefore stays
        # linked to the Sale Order's Transfers list).
        created_returns = self.env['stock.picking']
        if company and getattr(company, 'auto_create_return_picking', True):
            created_returns = self._create_return_for_done_pickings(captured, reason)

        # Then cancel the SO. Skip if we were CALLED from the SO cancel
        # hook (avoids recursion).
        if not self.env.context.get('accurate_skip_so_cancel'):
            self._cancel_sale_order_if_any(reason=reason)

        # Revive any returns the SO cancel cascade killed. They share the
        # procurement group with the cancelled SO, so Odoo's group-level
        # cancel cascades to them — we explicitly reset both picking AND
        # its moves back to draft, then confirm+assign so the warehouse
        # can receive the goods.
        for ret in created_returns:
            ret.invalidate_recordset()
            if ret.state == 'cancel':
                try:
                    # Reset cancelled moves back to draft so action_confirm
                    # has something to work with. Bypass any tracking guards
                    # by writing the state directly.
                    for mv in ret.move_ids:
                        if mv.state == 'cancel':
                            mv.write({'state': 'draft'})
                    ret.write({'state': 'draft'})
                    ret.action_confirm()
                    try:
                        ret.action_assign()
                    except Exception:
                        # If nothing to reserve (e.g. no stock yet) the picking
                        # stays in 'confirmed' — that's fine, warehouse can
                        # validate it when goods arrive physically.
                        pass
                    self._chatter(
                        'Return picking <b>%s</b> revived after SO cancel '
                        'cascade — Ready to validate.' % ret.name
                    )
                except Exception as exc:
                    _logger.warning(
                        'Accurate: could not revive return %s after SO '
                        'cancel: %s', ret.name, exc,
                    )

    # ── Reversal helpers (shared between returned + cancelled flows) ──────────

    def _chatter(self, body):
        """Post a chatter message on this shipment. The overridden
        message_post() automatically mirrors it to the linked Sale Order
        so the salesperson sees the full timeline without opening the
        shipment record.
        """
        self.ensure_one()
        self.message_post(body=body)

    def _cancel_sale_order_if_any(self, reason='Shipment reversed'):
        """Cancel the linked Sale Order so the operations dashboard reflects
        reality. We only act if the SO is still in a non-terminal state and
        has no other active shipments on it.
        """
        self.ensure_one()
        sale = self.sale_id
        if not sale:
            return
        if sale.state in ('cancel', 'draft'):
            return
        # If the SO has another shipment that is still active, don't cancel
        # the SO — the user may be doing partial-shipment / split deliveries.
        active_siblings = sale.accurate_shipment_ids.filtered(
            lambda s: s.state in ('draft', 'sent', 'delivered') and s.id != self.id
        )
        if active_siblings:
            self._chatter(
                'Sale Order <b>%s</b> not cancelled — it has %d other '
                'active shipment(s). Cancel them first if you want the '
                'order cancelled.' % (sale.name, len(active_siblings))
            )
            return
        try:
            # In Odoo 17/18, sale.action_cancel() opens a confirmation
            # wizard — it does NOT actually cancel. The private
            # _action_cancel() is what performs the cancel. Try it first.
            # Fall back to public action_cancel() for older versions where
            # _action_cancel doesn't exist or to handle Odoo 19's renames.
            if hasattr(sale, '_action_cancel'):
                sale._action_cancel()
            elif hasattr(sale, 'action_cancel'):
                sale.action_cancel()
            self._chatter('Sale Order <b>%s</b> cancelled (%s).' % (sale.name, reason))
        except Exception as exc:
            _logger.warning(
                'Accurate: failed to cancel Sale Order %s: %s', sale.name, exc
            )
            self._chatter(
                '<b>Warning:</b> Could not auto-cancel Sale Order '
                '<b>%s</b>. Please cancel manually. Error: %s'
                % (sale.name, exc)
            )

    def _handle_pickings_on_reverse(self, reason='Shipment reversed'):
        """Cancel pending pickings and/or create a return picking when a
        shipment is cancelled or returned.

        Rules (per Delivery Company config flags):
          - auto_cancel_pickings (default True):
              Pickings in {draft, waiting, confirmed, assigned} → call
              picking._action_cancel() so the warehouse dashboard is clean.
              Cancelled in dependency-correct order (Ship before Pick) so
              chained moves don't refuse.
          - auto_create_return_picking (default True):
              Pickings already in 'done' state → spawn a stock return
              picking via the standard `stock.return.picking` wizard so
              warehouse staff can receive the goods back.
        """
        self.ensure_one()
        company = self.delivery_company_id
        if not company:
            return

        # Collect every picking linked to this shipment (via the SO chain
        # OR the direct picking_id link). Multi-step warehouses spawn
        # multiple pickings per SO so we walk the whole chain.
        pickings = self.env['stock.picking']
        if self.sale_id:
            pickings |= self.sale_id.picking_ids
        if self.picking_id:
            pickings |= self.picking_id
        if not pickings:
            return

        if getattr(company, 'auto_cancel_pickings', True):
            self._cancel_pending_pickings(pickings)
        if getattr(company, 'auto_create_return_picking', True):
            self._create_return_for_done_pickings(pickings, reason)

    def _cancel_pending_pickings(self, pickings):
        """Cancel all pickings that are not yet validated. Try Ship first,
        then Pick, so chained moves don't complain.
        """
        self.ensure_one()
        pending = pickings.filtered(
            lambda p: p.state in ('draft', 'waiting', 'confirmed', 'assigned')
        )
        if not pending:
            return
        # Order: outgoing first, then internal (Ship before Pick)
        ordered = pending.sorted(
            lambda p: 0 if p.picking_type_code == 'outgoing' else 1
        )
        cancelled_names = []
        for p in ordered:
            try:
                # Odoo renamed the cancel method across versions:
                #   Odoo 19+ : action_cancel (public)
                #   Odoo 16-18: _action_cancel (private)
                # Try both, fall back to direct state write as last resort.
                if hasattr(p, 'action_cancel'):
                    p.action_cancel()
                elif hasattr(p, '_action_cancel'):
                    p._action_cancel()
                else:
                    p.write({'state': 'cancel'})
                cancelled_names.append(p.name)
            except Exception as exc:
                _logger.warning(
                    'Accurate: failed to cancel picking %s: %s', p.name, exc
                )
                self._chatter('<b>Warning:</b> Could not cancel picking <b>%s</b>: %s'
                              % (p.name, exc))
        if cancelled_names:
            self._chatter('Cancelled pending picking(s): <b>%s</b>.'
                          % ', '.join(cancelled_names))

    def _create_return_for_done_pickings(self, pickings, reason):
        """For each picking already in 'done' state, spawn a return picking
        using the standard stock.return.picking wizard with explicit lines.

        We only return the OUTGOING picking (or the dispatch picking in
        multi-step setups) — those are the ones that physically left the
        warehouse to the customer / courier. Returning intermediate Pick or
        Pack steps would just shuffle stock around inside the warehouse.
        """
        self.ensure_one()
        created_returns = self.env['stock.picking']
        candidates = pickings.filtered(lambda p: p.state == 'done')
        outgoing_done = candidates.filtered(lambda p: p.picking_type_code == 'outgoing')
        target_pickings = outgoing_done or candidates
        if not target_pickings:
            return created_returns

        # The 'completed quantity' field on stock.move was renamed across
        # versions: Odoo 19 = `quantity`, Odoo 16-18 = `quantity_done`,
        # very old = `product_uom_qty`. Fall back through the list.
        def _move_qty(move):
            for fname in ('quantity', 'quantity_done', 'product_uom_qty'):
                if fname in move._fields:
                    val = getattr(move, fname, 0.0) or 0.0
                    if val:
                        return val
            return 0.0

        ReturnWizard = self.env['stock.return.picking']
        Picking = self.env['stock.picking']
        for src in target_pickings:
            # Skip if a return picking for this source already exists
            existing = Picking.search([
                ('origin', '=', 'Return of %s' % src.name),
                ('state', '!=', 'cancel'),
            ], limit=1)
            if existing:
                self.message_post(
                    body='Return picking <b>%s</b> already exists for <b>%s</b>, skipping.'
                         % (existing.name, src.name)
                )
                continue

            # Build explicit return-move lines so we don't depend on the
            # wizard's default_get behavior (which differs by Odoo version).
            return_lines = []
            for m in src.move_ids:
                if m.state != 'done':
                    continue
                qty = _move_qty(m)
                if qty <= 0:
                    continue
                return_lines.append((0, 0, {
                    'product_id': m.product_id.id,
                    'quantity': qty,
                    'move_id': m.id,
                    'uom_id': m.product_uom.id,
                }))
            if not return_lines:
                self._chatter(
                    'No delivered moves found on <b>%s</b> — return picking '
                    'would be empty, so skipping.' % src.name
                )
                continue

            try:
                wizard = ReturnWizard.with_context(
                    active_id=src.id,
                    active_ids=src.ids,
                    active_model='stock.picking',
                ).create({
                    'picking_id': src.id,
                    'product_return_moves': return_lines,
                })
                action = (wizard.action_create_returns()
                          if hasattr(wizard, 'action_create_returns')
                          else wizard.create_returns())
                new_pid = (action or {}).get('res_id') if isinstance(action, dict) else None
                new_pick = Picking.browse(new_pid) if new_pid else Picking
                if new_pick:
                    # Keep the group_id intact so the return picking still
                    # appears in the Sale Order's Transfers list. Disable
                    # cancel-propagation on the moves so a sibling SO cancel
                    # cascade doesn't kill this fresh return.
                    for mv in new_pick.move_ids:
                        if 'propagate_cancel' in mv._fields:
                            try:
                                mv.propagate_cancel = False
                            except Exception:
                                pass
                    # If something already cancelled it, revive it to assigned.
                    if new_pick.state == 'cancel':
                        try:
                            if hasattr(new_pick, 'action_back_to_draft'):
                                new_pick.action_back_to_draft()
                            new_pick.action_confirm()
                            new_pick.action_assign()
                        except Exception as exc:
                            _logger.warning(
                                'Accurate: return picking %s was cancelled and '
                                'could not be revived: %s', new_pick.name, exc
                            )
                    self._chatter(
                        ('Return picking <b>%s</b> created from <b>%s</b> '
                         'with %d line(s) — validate it when goods arrive '
                         'physically.')
                        % (new_pick.name, src.name, len(return_lines))
                    )
                    created_returns |= new_pick
                else:
                    self._chatter(
                        'Return wizard ran for <b>%s</b> but did not return a picking id.'
                        % src.name
                    )
            except Exception as exc:
                _logger.warning(
                    'Accurate: failed to create return picking for %s: %s', src.name, exc
                )
                self._chatter(
                    '<b>Warning:</b> Could not auto-create return picking for '
                    '<b>%s</b>. Please create it manually. Error: %s'
                    % (src.name, exc)
                )
        return created_returns

    def _reverse_invoice_if_any(self, reason='Shipment reversed'):
        """If an invoice was created for this shipment, reverse it sensibly:
          - Posted/paid invoice → create a customer credit note (account.move.reversal)
          - Draft invoice → button_cancel
          - Already cancelled → no-op
        """
        self.ensure_one()
        if not self.invoice_id:
            return
        invoice = self.invoice_id
        if invoice.state == 'cancel':
            return
        if invoice.state == 'draft':
            try:
                invoice.button_cancel()
                self._chatter('Cancelled draft invoice <b>%s</b>.' % invoice.name)
            except Exception as exc:
                _logger.warning(
                    'Accurate: failed to cancel draft invoice %s: %s', invoice.name, exc,
                )
            return
        # Posted invoice → create credit note
        try:
            wizard = self.env['account.move.reversal'].with_context(
                active_model='account.move', active_ids=invoice.ids,
            ).create({
                'reason': reason,
                'journal_id': invoice.journal_id.id,
            })
            if 'reverse_method' in wizard._fields:
                wizard.reverse_method = 'cancel'
            action = wizard.refund_moves() if hasattr(wizard, 'refund_moves') else \
                     wizard.reverse_moves()
            credit_id = (action or {}).get('res_id') if isinstance(action, dict) else None
            credit = self.env['account.move'].browse(credit_id) if credit_id else self.env['account.move']
            if credit:
                self._chatter(
                    'Credit note <b>%s</b> created (reverses invoice <b>%s</b>).'
                    % (credit.name or '—', invoice.name)
                )
        except Exception as exc:
            _logger.warning(
                'Accurate: failed to reverse invoice %s for shipment %s: %s',
                invoice.name, self.name, exc,
            )
            self._chatter(
                '<b>Warning:</b> Could not auto-reverse invoice <b>%s</b>. '
                'Please review manually. Error: %s' % (invoice.name, exc)
            )

    def _reverse_expense_if_any(self, reason='Shipment reversed'):
        """Reverse the shipping-fee expense entry (if any) by creating a
        mirror journal entry.
        """
        self.ensure_one()
        if not self.expense_move_id or self.expense_move_id.state == 'cancel':
            return
        try:
            reversal = self.expense_move_id._reverse_moves(
                default_values_list=[{
                    'date': fields.Date.today(),
                    'ref': '%s — %s' % (reason, self.expense_move_id.ref or ''),
                }],
                cancel=True,
            )
            self._chatter(
                'Shipping-expense entry <b>%s</b> reversed by <b>%s</b>.'
                % (self.expense_move_id.name, reversal[:1].name or '—')
            )
        except Exception as exc:
            _logger.warning(
                'Accurate: failed to reverse expense entry %s for shipment %s: %s',
                self.expense_move_id.name, self.name, exc,
            )
            self._chatter(
                '<b>Warning:</b> Could not auto-reverse shipping-expense '
                'entry <b>%s</b>. Please review manually.'
                % self.expense_move_id.name
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

    def action_open_cancel_wizard(self):
        """Open the cancel-shipment wizard. The wizard collects a cancellation
        reason and calls the Accurate API + the local _on_cancelled() flow.
        """
        self.ensure_one()
        if self.state in ('cancelled', 'returned'):
            raise UserError(
                'Shipment is already in state "%s" — nothing to cancel.\n'
                'الشحنة بالفعل في حالة "%s".' % (self.state, self.state)
            )
        if not self.api_id:
            raise UserError(
                'Shipment has not been sent to Accurate Logistics yet.\n'
                'الشحنة لم يتم إرسالها إلى أكيوريت لوجيستكس بعد.'
            )
        # Make sure cancellation reasons are available — guide the user if not.
        if not self.env['accurate.cancellation.reason'].search_count([]):
            raise UserError(
                'No cancellation reasons synced yet.\n'
                'Open the Delivery Company form and click "Sync Cancellation '
                'Reasons" first.\n\n'
                'لم تتم مزامنة أسباب الإلغاء بعد. افتح شركة الشحن واضغط على '
                'زر "مزامنة أسباب الإلغاء".'
            )
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cancel Shipment',
            'res_model': 'accurate.cancel.shipment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_shipment_id': self.id,
            },
        }

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
        # Sync any non-terminal shipment so we also catch returns and
        # cancellations triggered after delivery (e.g. RTRN reached after DTR).
        # Only sync shipments whose Delivery Company has auto-sync enabled.
        enabled_companies = self.env['accurate.delivery.company'].search([
            ('cron_sync_enabled', '=', True),
        ])
        if not enabled_companies:
            _logger.info('Accurate Logistics cron: no companies have auto-sync enabled.')
            return
        pending = self.search([
            ('state', 'in', ('sent', 'delivered')),
            ('api_id', '!=', False),
            ('delivery_company_id', 'in', enabled_companies.ids),
        ])
        _logger.info('Accurate Logistics cron: syncing %d shipments.', len(pending))
        for rec in pending:
            try:
                if not rec.delivery_company_id:
                    continue
                data = rec.delivery_company_id._al_get_shipment(api_id=rec.api_id, code=rec.code)
                if not data:
                    continue
                rec._apply_api_response(data)
                # State-based dispatch — fire whenever the status maps to a
                # family and the shipment isn't already in that terminal
                # state. _on_* handlers are idempotent, so this also catches
                # up shipments synced before the flow logic existed.
                company = rec.delivery_company_id
                code, name = rec.api_status_code, rec.api_status_name
                if company._is_delivered_code(code, name) and not rec.invoice_id and rec.state != 'delivered':
                    rec._on_delivered()
                elif company._is_returned_code(code, name) and rec.state != 'returned':
                    rec._on_returned()
                elif company._is_cancelled_code(code, name) and rec.state != 'cancelled':
                    rec._on_cancelled()
            except Exception as exc:
                _logger.warning('Accurate cron: failed for %s: %s', rec.name, exc)
