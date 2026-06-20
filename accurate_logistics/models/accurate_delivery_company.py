import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccurateDeliveryCompany(models.Model):
    _name = 'accurate.delivery.company'
    _description = 'Accurate Delivery Company'
    _inherit = ['accurate.api.mixin', 'mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'
    _order = 'name'

    # ── Basic info ────────────────────────────────────────────────────────────

    name = fields.Char('Company Name', required=True, tracking=True)
    active = fields.Boolean('Active', default=True)
    notes = fields.Text('Notes')
    logo = fields.Image(
        'Logo', max_width=512, max_height=512,
        help='Logo for this delivery company. Shown on the Sale Order and '
             'Invoice PDF reports when this company handles the order.',
    )

    # ── API Connection (per company) ──────────────────────────────────────────

    api_url = fields.Char(
        'API Endpoint',
        default='https://marsool.lg.accuratess.com:8001/graphql',
        help='GraphQL endpoint URL for this delivery company.',
    )
    api_username = fields.Char('Username')
    api_password = fields.Char('Password')
    ssl_verify = fields.Boolean(
        'Verify SSL Certificate',
        default=True,
        help='Disable only if the server uses a self-signed certificate.',
    )
    api_branch_id = fields.Integer(
        'Branch ID',
        help='Optional. If your Accurate account has multiple branches, set the '
             'Branch ID to fetch zones for that branch (matches the dashboard '
             'zone list). Leave empty to use the default.',
    )
    api_country_id = fields.Integer(
        'Country ID',
        help='Optional. Filter zones by country ID if your account spans multiple countries.',
    )
    # Token cache — managed automatically, not shown to users
    api_token = fields.Char(copy=False)
    api_token_expiry = fields.Datetime(copy=False)

    # Background sync state
    sync_in_progress = fields.Boolean('Sync in Progress', default=False, copy=False)
    sync_cancel_requested = fields.Boolean(
        'Cancel Sync Requested', default=False, copy=False,
        help='Set to True by the Stop Sync button. Background workers check '
             'this flag periodically and exit early when set.',
    )
    sync_progress = fields.Char('Sync Progress', copy=False, default='')

    # Discovered branches (from Test Connection probe)
    branch_ids = fields.One2many(
        'accurate.branch',
        'company_id',
        string='Discovered Branches',
        help='Branches found by probing the API. Use the Branch ID field above '
             'to scope the zone sync to a specific branch.',
    )
    branch_count = fields.Integer(compute='_compute_branch_count')

    @api.depends('branch_ids')
    def _compute_branch_count(self):
        for rec in self:
            rec.branch_count = len(rec.branch_ids)

    # ── Accounting ────────────────────────────────────────────────────────────

    journal_id = fields.Many2one(
        'account.journal',
        string='COD Journal',
        required=True,
        domain=[('type', 'in', ['cash', 'bank'])],
        tracking=True,
        help='COD payments will be posted to this journal when delivery is confirmed.',
    )
    expense_account_id = fields.Many2one(
        'account.account',
        string='Shipping Expense Account',
        domain=[('account_type', '=', 'expense')],
        tracking=True,
        help='Account used to book the courier\'s delivery fee as an expense '
             'when a shipment uses "Shipping Fee Included in Price" (INCLD). '
             'On delivery, the fee deducted by the courier from the COD '
             'collection is debited to this account.',
    )
    delivered_status_codes = fields.Char(
        'Delivered Status Codes',
        default='DTR,DEL,DLV,DELIVERED',
        help='Comma-separated codes from Accurate that mean "Delivered" '
             '(triggers invoice + COD payment). Default: DTR (Delivered To Recipient).',
    )
    returned_status_codes = fields.Char(
        'Returned Status Codes',
        default='RTRN,RTS',
        help='Comma-separated codes from Accurate that mean the shipment was '
             'returned to sender. Triggers credit-note / cancel-invoice flow. '
             'Default: RTRN (Returned) and RTS (ارتجاع للراسل / Return to sender).',
    )
    cancelled_status_codes = fields.Char(
        'Cancelled Status Codes',
        default='RJCT,CANCELLED',
        help='Comma-separated codes from Accurate that mean the shipment was '
             'cancelled. Default: RJCT (Rejected) and CANCELLED.',
    )
    auto_cancel_pickings = fields.Boolean(
        'Auto-cancel pending pickings',
        default=True,
        help='When a shipment is cancelled or returned, automatically cancel '
             'any linked pickings that are NOT yet validated (state in '
             "draft / waiting / confirmed / assigned). Set to False to handle "
             'pickings manually.',
    )
    auto_create_return_picking = fields.Boolean(
        'Auto-create return picking',
        default=True,
        help='When a shipment is cancelled or returned and a delivery picking '
             'was already validated (state=done), automatically spawn a return '
             'picking to bring the goods back into stock. Set to False if you '
             'prefer to create returns manually.',
    )
    notify_user_ids = fields.Many2many(
        'res.users',
        'accurate_company_notify_user_rel',
        'company_id', 'user_id',
        string='Users to Notify',
        help='Users who receive an activity on the outgoing picking when the '
             'shipment is marked Delivered by the courier but the internal '
             'Pick/Pack step is still not done. The outgoing picking will '
             'NOT auto-validate in that case — these users must reconcile '
             'the warehouse first.',
    )

    # ── Auto-sync cron control ────────────────────────────────────────────────
    cron_sync_enabled = fields.Boolean(
        'Include in Auto-Sync',
        default=True,
        help="When on, the scheduled status-sync job syncs this company's "
             "shipments (sent / delivered) and fires the delivered / "
             "returned / cancelled flows. Turn off to exclude this company.",
    )
    cron_active = fields.Boolean(
        'Auto-Sync Scheduler Running',
        compute='_compute_cron_settings',
        inverse='_inverse_cron_active',
        help='Global on/off switch for the scheduled status-sync job. There '
             'is ONE shared scheduler, so this affects all companies.',
    )
    cron_interval_minutes = fields.Integer(
        'Run Every (minutes)',
        compute='_compute_cron_settings',
        inverse='_inverse_cron_interval',
        help='How often the scheduled status-sync runs, in minutes. Global '
             '(shared across all companies). Minimum 1.',
    )

    def _get_sync_cron(self):
        """The shared status-sync ir.cron record (or empty)."""
        return self.env.ref(
            'accurate_logistics.cron_accurate_sync_statuses',
            raise_if_not_found=False,
        )

    @api.depends_context('uid')
    def _compute_cron_settings(self):
        cron = self._get_sync_cron()
        active = bool(cron and cron.active)
        minutes = 30
        if cron:
            num = cron.interval_number or 0
            if cron.interval_type == 'hours':
                minutes = num * 60
            elif cron.interval_type == 'days':
                minutes = num * 1440
            else:
                minutes = num
        for rec in self:
            rec.cron_active = active
            rec.cron_interval_minutes = minutes

    def _inverse_cron_active(self):
        cron = self._get_sync_cron()
        if cron:
            cron.sudo().active = bool(self[:1].cron_active)

    def _inverse_cron_interval(self):
        cron = self._get_sync_cron()
        if cron:
            val = self[:1].cron_interval_minutes or 30
            cron.sudo().write({
                'interval_number': max(1, val),
                'interval_type': 'minutes',
            })

    # ── Per-company webhook ───────────────────────────────────────────────────
    webhook_secret = fields.Char(
        'Webhook Secret',
        copy=False,
        help='Secret token for this company\'s webhook. Paste the Callback '
             'URL below into THIS company\'s Accurate Logistics dashboard. '
             'Each company has its own secret so a leak of one does not '
             'expose the others.',
    )
    webhook_url = fields.Char(
        'Callback URL',
        compute='_compute_webhook_url',
        help='Copy this and set it as the Callback URL in this company\'s '
             'Accurate Logistics account.',
    )

    @api.depends('webhook_secret')
    def _compute_webhook_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for rec in self:
            if rec.webhook_secret:
                rec.webhook_url = '%s/accurate/webhook?secret=%s' % (base, rec.webhook_secret)
            else:
                rec.webhook_url = ''

    def action_generate_webhook_secret(self):
        self.ensure_one()
        self.webhook_secret = uuid.uuid4().hex
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Webhook Secret Generated',
                'message': (
                    'New secret generated for %s. Copy the Callback URL and '
                    'paste it into this company\'s Accurate Logistics dashboard.'
                ) % self.name,
                'type': 'success',
                'sticky': True,
            },
        }

    default_service_id = fields.Many2one(
        'accurate.service',
        string='Default Shipping Service',
        domain="[('company_id', '=', id)]",
        help='Required by Accurate Logistics on every shipment. '
             'If left empty, the first synced service for this company '
             'will be used.',
    )

    # ── Zones linked to this company ──────────────────────────────────────────

    zone_ids = fields.Many2many(
        'accurate.zone',
        'accurate_company_zone_rel',
        'company_id', 'zone_id',
        string='Zones',
        domain=[('is_subzone', '=', False)],
    )
    subzone_ids = fields.Many2many(
        'accurate.zone',
        'accurate_company_subzone_rel',
        'company_id', 'zone_id',
        string='Sub-zones',
        domain=[('is_subzone', '=', True)],
    )
    zone_count = fields.Integer('Zone Count', compute='_compute_zone_count')
    subzone_count = fields.Integer('Sub-zone Count', compute='_compute_zone_count')

    # ── Shipments ─────────────────────────────────────────────────────────────

    shipment_ids = fields.One2many('accurate.shipment', 'delivery_company_id', 'Shipments')
    shipment_count = fields.Integer(compute='_compute_counts')
    pending_count = fields.Integer(compute='_compute_counts')

    # ── Computes ──────────────────────────────────────────────────────────────

    @api.depends('zone_ids', 'subzone_ids')
    def _compute_zone_count(self):
        for rec in self:
            rec.zone_count = len(rec.zone_ids)
            rec.subzone_count = len(rec.subzone_ids)

    @api.depends('shipment_ids', 'shipment_ids.state')
    def _compute_counts(self):
        for rec in self:
            rec.shipment_count = len(rec.shipment_ids)
            rec.pending_count = len(rec.shipment_ids.filtered(lambda s: s.state == 'sent'))

    # ── API connection test ───────────────────────────────────────────────────

    def action_test_connection(self):
        """Authenticate, then probe branches + sync services so the user
        sees everything available in this Accurate account at once."""
        self.ensure_one()
        self._al_test_connection()  # forces a fresh login

        Branch = self.env['accurate.branch']
        Service = self.env['accurate.service']

        # ── Discover branches by probing branchId 1..20 ───────────────────
        Branch.search([('company_id', '=', self.id)]).unlink()
        branches_created = 0
        unfiltered_total = 0
        try:
            unfiltered = self._al_list_zones() or []
            unfiltered_total = len(unfiltered)
        except Exception as exc:
            _logger.warning('Unfiltered zone probe failed: %s', exc)

        for bid in range(1, 21):
            try:
                zones = self._al_list_zones(filter_input={'branchId': bid}) or []
            except Exception:
                zones = []
            if not zones:
                continue
            sample = zones[0].get('name', '')
            Branch.create({
                'company_id': self.id,
                'api_id': bid,
                'sample_zone': sample,
                'zone_count': len(zones),
            })
            branches_created += 1

        # ── Sync services dropdown (scoped to THIS company) ──────────────
        services_synced = 0
        try:
            services = self._al_list_services() or []
            for s in services:
                s_id = s.get('id')
                s_name = s.get('name', '')
                if not s_id:
                    continue
                existing = Service.search([
                    ('api_id', '=', s_id),
                    ('company_id', '=', self.id),
                ], limit=1)
                vals = {
                    'api_id': s_id,
                    'name': s_name,
                    'company_id': self.id,
                }
                if existing:
                    existing.write(vals)
                else:
                    Service.create(vals)
                services_synced += 1
        except Exception as exc:
            _logger.warning('Service sync failed: %s', exc)

        msg = (
            'Connected ✔\n'
            'Discovered %d branches.\n'
            'Total zones (no filter): %d\n'
            'Synced %d services.\n'
            'See the "Branches" tab below to choose a Branch ID.'
        ) % (branches_created, unfiltered_total, services_synced)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Connection Successful',
                'message': msg,
                'type': 'success',
                'sticky': True,
            },
        }

    # ── Zone sync actions ─────────────────────────────────────────────────────

    def _al_scope(self):
        """Build the filter dict using branch/country scope set on this company."""
        scope = {}
        if self.api_branch_id:
            scope['branchId'] = self.api_branch_id
        if self.api_country_id:
            scope['countryId'] = self.api_country_id
        return scope

    def action_sync_zones(self):
        """Pull zones from the API (scoped by Branch/Country) and link them."""
        self.ensure_one()
        scope = self._al_scope()
        zones = self._al_list_zones(filter_input=scope or None)
        if not zones:
            raise UserError(
                'No zones returned from the API for the current scope.\n'
                'Check Branch ID / Country ID and credentials.'
            )

        Zone = self.env['accurate.zone']
        synced = Zone._upsert_zones(zones, is_subzone=False, company=self)
        scope_label = ''
        if scope:
            parts = []
            if 'branchId' in scope:
                parts.append('branch %d' % scope['branchId'])
            if 'countryId' in scope:
                parts.append('country %d' % scope['countryId'])
            scope_label = ' (' + ', '.join(parts) + ')'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Zones Synced',
                'message': 'Synced %d zones for %s%s.' % (synced, self.name, scope_label),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_sync_subzones(self):
        """Pull sub-zones for every zone linked to this company.

        Commits in batches so a network drop / browser timeout doesn't
        wipe the progress already made.
        """
        self.ensure_one()
        if not self.zone_ids:
            raise UserError('No zones linked to this company. Sync zones first.')

        Zone = self.env['accurate.zone']
        synced = 0
        BATCH = 25
        company_id = self.id

        scope = self._al_scope()
        for idx, parent in enumerate(self.zone_ids, start=1):
            f = dict(scope)
            f['parentId'] = parent.api_id
            try:
                subzones = self._al_list_zones(filter_input=f)
            except Exception:
                subzones = []

            batch_ids = []
            for z in subzones:
                z_id = z.get('id')
                z_name = z.get('name', '')
                if not z_id:
                    continue
                existing = Zone.search(
                    [('api_id', '=', z_id), ('is_subzone', '=', True)], limit=1
                )
                vals = {
                    'api_id': z_id,
                    'name': z_name,
                    'is_subzone': True,
                    'parent_id': parent.id,
                }
                if existing:
                    existing.write(vals)
                    batch_ids.append(existing.id)
                else:
                    rec = Zone.create(vals)
                    batch_ids.append(rec.id)
                synced += 1

            if batch_ids:
                self.browse(company_id).write(
                    {'subzone_ids': [(4, sid) for sid in batch_ids]}
                )

            # Save progress every BATCH parents — survives disconnects.
            if idx % BATCH == 0:
                self.env.cr.commit()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sub-zones Synced',
                'message': 'Synced %d sub-zones for %s.' % (synced, self.name),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_sync_all(self):
        """One-click: sync zones AND sub-zones in a background thread.

        Returns immediately so the browser does not time out. Progress is
        written to ``sync_progress`` and saved every batch — refresh the
        form to watch it advance.
        """
        self.ensure_one()
        if self.sync_in_progress:
            raise UserError(
                'A sync is already running for this company. '
                'Refresh the form to see progress.'
            )

        # Mark the sync as starting and commit so the worker thread sees it.
        self.write({
            'sync_in_progress': True,
            'sync_cancel_requested': False,
            'sync_progress': 'Starting…',
        })
        self.env.cr.commit()

        company_id = self.id
        db_name = self.env.cr.dbname
        uid = self.env.uid

        def _worker():
            registry = self.pool
            try:
                with registry.cursor() as new_cr:
                    env = api.Environment(new_cr, uid, {})
                    env['accurate.delivery.company'].browse(company_id)._do_sync_all()
            except Exception as exc:
                _logger.exception('Accurate background sync failed: %s', exc)
                # Best-effort: clear the flag so the user can retry.
                try:
                    with registry.cursor() as cr2:
                        env2 = api.Environment(cr2, uid, {})
                        env2['accurate.delivery.company'].browse(company_id).write({
                            'sync_in_progress': False,
                            'sync_progress': 'ERROR: %s' % exc,
                        })
                        cr2.commit()
                except Exception:
                    pass

        threading.Thread(target=_worker, name='accurate-sync-%d' % company_id, daemon=True).start()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync Started',
                'message': (
                    'Zones and sub-zones are syncing in the background. '
                    'Refresh this page after a few minutes to watch progress.'
                ),
                'type': 'info',
                'sticky': False,
            },
        }

    def _do_sync_all(self):
        """Heavy sync work — runs inside the worker thread on its own cursor."""
        self.ensure_one()
        Zone = self.env['accurate.zone']

        # 1. Zones — apply optional branch/country scope
        self.write({'sync_progress': 'Fetching zones…'})
        self.env.cr.commit()
        scope = self._al_scope()
        zones = self._al_list_zones(filter_input=scope or None)
        if not zones:
            self.write({'sync_in_progress': False, 'sync_progress': 'No zones returned'})
            self.env.cr.commit()
            return
        zone_count = Zone._upsert_zones(zones, is_subzone=False, company=self)
        self.write({'sync_progress': 'Synced %d zones, fetching sub-zones…' % zone_count})
        self.env.cr.commit()

        # 2. Sub-zones — fetch in parallel (API calls), persist sequentially.
        subzone_count = 0
        total = len(self.zone_ids)
        parents = list(self.zone_ids)  # snapshot recordset to plain list
        WORKERS = 10
        REPORT_EVERY = 20  # update progress field every N parents

        # Pre-warm the auth token so all worker threads share one instead
        # of racing to log in concurrently.
        try:
            self._al_get_token()
        except Exception as exc:
            _logger.warning('Pre-auth failed: %s', exc)

        def _fetch(parent):
            f = dict(scope) if scope else {}
            f['parentId'] = parent.api_id
            try:
                subs = self._al_list_zones(filter_input=f)
            except Exception as exc:
                _logger.warning('Sub-zone fetch failed for %s: %s', parent.name, exc)
                subs = []
            return parent.id, subs

        processed = 0
        cancelled = False
        with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix='accurate-subzone') as pool:
            future_to_parent = {pool.submit(_fetch, p): p for p in parents}
            for future in as_completed(future_to_parent):
                parent_odoo_id, subs = future.result()
                processed += 1

                batch_ids = []
                for z in subs:
                    z_id = z.get('id')
                    z_name = z.get('name', '')
                    if not z_id:
                        continue
                    existing = Zone.search(
                        [('api_id', '=', z_id), ('is_subzone', '=', True)], limit=1
                    )
                    vals = {
                        'api_id': z_id,
                        'name': z_name,
                        'is_subzone': True,
                        'parent_id': parent_odoo_id,
                    }
                    if existing:
                        existing.write(vals)
                        batch_ids.append(existing.id)
                    else:
                        rec = Zone.create(vals)
                        batch_ids.append(rec.id)
                    subzone_count += 1

                if batch_ids:
                    self.write({'subzone_ids': [(4, sid) for sid in batch_ids]})

                if processed % REPORT_EVERY == 0 or processed == total:
                    # Check cancel flag — re-read fresh from DB
                    self.env.cr.commit()
                    self.env.cr.execute(
                        'SELECT sync_cancel_requested FROM '
                        'accurate_delivery_company WHERE id = %s',
                        (self.id,),
                    )
                    row = self.env.cr.fetchone()
                    if row and row[0]:
                        cancelled = True
                        for f in future_to_parent:
                            if not f.done():
                                f.cancel()
                        self.write({
                            'sync_progress': (
                                'Cancelled at %d / %d zones (%d subs synced).'
                                % (processed, total, subzone_count)
                            ),
                        })
                        self.env.cr.commit()
                        break
                    self.write({
                        'sync_progress': 'Sub-zones: %d / %d zones processed (%d subs synced)' % (
                            processed, total, subzone_count,
                        ),
                    })
                    self.env.cr.commit()

        final_msg = (
            'Cancelled — %d zones synced, %d sub-zones synced before stop.'
            % (zone_count, subzone_count)
        ) if cancelled else (
            'Done — %d zones, %d sub-zones' % (zone_count, subzone_count)
        )
        self.write({
            'sync_in_progress': False,
            'sync_cancel_requested': False,
            'sync_progress': final_msg,
        })
        self.env.cr.commit()

    def action_clear_zones(self):
        """Remove all zone/subzone links from this company (does not delete zones)."""
        self.ensure_one()
        self.write({'zone_ids': [(5,)], 'subzone_ids': [(5,)]})

    def action_force_reset_sync(self):
        """Clear sync_in_progress / sync_cancel_requested without waiting for
        the worker. Use when the worker is stuck (slow API, network drop) and
        the polite cancel didn't take effect within a reasonable time.

        Note: the orphan worker thread may still finish in the background and
        write its final state. That's fine — it just overwrites our reset.
        """
        self.ensure_one()
        self.write({
            'sync_in_progress': False,
            'sync_cancel_requested': False,
            'sync_progress': 'Force-reset by user.',
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync Force-Reset',
                'message': 'Sync flags cleared. You can start a new sync now.',
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_cancel_sync(self):
        """Request the running background sync (zones / subzones / price-list
        validation) to stop. The worker checks this flag between batches and
        exits cleanly. UI returns immediately; cancellation may take up to a
        few seconds while the current batch finishes.
        """
        self.ensure_one()
        if not self.sync_in_progress:
            raise UserError('No sync is currently running.')
        self.write({
            'sync_cancel_requested': True,
            'sync_progress': (self.sync_progress or '') + ' [cancel requested]',
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Cancel Requested',
                'message': 'Background sync will stop after the current batch.',
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_validate_price_list(self):
        """Probe each sub-zone via calculateShipmentFees to find which ones
        the merchant has rates for, mark them in_price_list=True and the
        rest in_price_list=False. Runs in a background thread so the
        browser doesn't time out.
        """
        self.ensure_one()
        if not self.default_service_id or not self.default_service_id.api_id:
            raise UserError(
                'Set a Default Shipping Service on this Delivery Company '
                'before validating the price list.'
            )
        if self.sync_in_progress:
            raise UserError(
                'A sync is already running. Wait for it to finish, or refresh '
                'and check the progress message.'
            )
        self.write({
            'sync_in_progress': True,
            'sync_cancel_requested': False,
            'sync_progress': 'Starting price-list validation…',
        })
        self.env.cr.commit()

        threading.Thread(
            target=self._do_validate_price_list,
            args=(self.env.cr.dbname, self.id, self.env.uid),
            daemon=True,
        ).start()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Price-List Validation Started',
                'message': 'Probing all sub-zones via calculateShipmentFees. '
                           'Refresh the page to see progress.',
                'type': 'info',
                'sticky': True,
            },
        }

    def _do_validate_price_list(self, dbname, company_id, uid):
        """Background worker: probe every sub-zone and mark in_price_list."""
        from odoo import api as odoo_api, registry as odoo_registry, SUPERUSER_ID
        registry = odoo_registry(dbname)
        valid_count = 0
        invalid_count = 0
        try:
            with registry.cursor() as cr:
                env = odoo_api.Environment(cr, uid, {})
                company = env['accurate.delivery.company'].browse(company_id)
                if not company.exists():
                    return
                service_id = company.default_service_id.api_id
                subzones = company.subzone_ids.sorted('id')
                total = len(subzones)

                # Build the probe list in memory (id, parent_api, sub_api)
                probes = []
                for sub in subzones:
                    if not sub.parent_id.api_id or not sub.api_id:
                        continue
                    probes.append((sub.id, sub.parent_id.api_id, sub.api_id))

                # Local probe fn — uses a fresh requests context per worker
                def _probe(triple):
                    sub_id, parent_api, sub_api = triple
                    fee_input = {
                        'serviceId': service_id,
                        'recipientZoneId': parent_api,
                        'recipientSubzoneId': sub_api,
                        'weight': 0.5,
                        'price': 1.0,
                        'paymentTypeCode': 'COLC',
                        'priceTypeCode': 'EXCLD',
                        'typeCode': 'FDP',
                    }
                    try:
                        fees = company._al_calculate_fees(fee_input)
                        return (sub_id, bool(fees and fees.get('total') is not None))
                    except Exception:
                        return (sub_id, False)

                # Run probes in parallel — 10 workers ≈ 8x speedup
                results = []
                cancelled = False
                with ThreadPoolExecutor(max_workers=10) as ex:
                    futures = {ex.submit(_probe, p): p for p in probes}
                    done = 0
                    for fut in as_completed(futures):
                        try:
                            sub_id, valid = fut.result()
                            results.append((sub_id, valid))
                            if valid:
                                valid_count += 1
                            else:
                                invalid_count += 1
                        except Exception:
                            invalid_count += 1
                        done += 1
                        # Check cancel flag every 5 — cheap re-read from DB
                        if done % 5 == 0:
                            cr.commit()
                            cr.execute(
                                'SELECT sync_cancel_requested FROM '
                                'accurate_delivery_company WHERE id = %s',
                                (company_id,),
                            )
                            row = cr.fetchone()
                            if row and row[0]:
                                cancelled = True
                                # Stop submitting; cancel pending futures
                                for f in futures:
                                    if not f.done():
                                        f.cancel()
                                company.sync_progress = (
                                    'Cancelled at %d/%d (valid=%d, invalid=%d).'
                                    % (done, total, valid_count, invalid_count)
                                )
                                cr.commit()
                                break
                            company.sync_progress = (
                                'Validating %d/%d (valid=%d, invalid=%d)…'
                                % (done, total, valid_count, invalid_count)
                            )
                            cr.commit()

                # Bulk write results — group by validity for fewer SQL roundtrips
                now = fields.Datetime.now()
                valid_ids = [sid for sid, ok in results if ok]
                invalid_ids = [sid for sid, ok in results if not ok]
                Zone = env['accurate.zone']
                if valid_ids:
                    Zone.browse(valid_ids).write({
                        'in_price_list': True,
                        'price_list_validated_at': now,
                    })
                if invalid_ids:
                    Zone.browse(invalid_ids).write({
                        'in_price_list': False,
                        'price_list_validated_at': now,
                    })

                final_msg = (
                    ('Price-list validation cancelled. Partial results: '
                     '%d valid, %d invalid sub-zones (%d/%d processed).'
                     % (valid_count, invalid_count, len(results), total))
                    if cancelled else
                    ('Price-list validation done. %d valid, %d invalid sub-zones.'
                     % (valid_count, invalid_count))
                )
                company.write({
                    'sync_in_progress': False,
                    'sync_cancel_requested': False,
                    'sync_progress': final_msg,
                })
                cr.commit()
        except Exception as exc:
            _logger.exception('Accurate price-list validation failed: %s', exc)
            try:
                with registry.cursor() as cr:
                    env = odoo_api.Environment(cr, uid, {})
                    env['accurate.delivery.company'].browse(company_id).write({
                        'sync_in_progress': False,
                        'sync_progress': 'Validation failed: %s' % exc,
                    })
                    cr.commit()
            except Exception:
                pass

    def action_sync_cancellation_reasons(self):
        """Sync the master list of cancellation reasons from the API."""
        self.ensure_one()
        try:
            reasons = self._al_list_cancellation_reasons()
        except Exception as exc:
            raise UserError(
                'Failed to fetch cancellation reasons from Accurate Logistics:\n%s' % exc
            )
        result = self.env['accurate.cancellation.reason']._upsert_from_api(reasons, company=self)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Cancellation Reasons Synced',
                'message': '%d created, %d updated. Total reasons: %d.' % (
                    result.get('created', 0),
                    result.get('updated', 0),
                    self.env['accurate.cancellation.reason'].search_count([]),
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    # ── Smart button actions ──────────────────────────────────────────────────

    def action_view_zones(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Zones – %s' % self.name,
            'res_model': 'accurate.zone',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.zone_ids.ids)],
        }

    def action_view_subzones(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sub-zones – %s' % self.name,
            'res_model': 'accurate.zone',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.subzone_ids.ids)],
        }

    def action_view_shipments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Shipments – %s' % self.name,
            'res_model': 'accurate.shipment',
            'view_mode': 'list,form',
            'domain': [('delivery_company_id', '=', self.id)],
            'context': {'default_delivery_company_id': self.id},
        }

    # ── Status matching ─────────────────────────────────────────────────────
    #
    # Some Accurate tenants send the status with only {id, name} and NO code
    # (e.g. {"id": 475, "name": "ارتجاع للراسل"}). So matching must work on
    # the code, the name, OR the id. We check the configured comma-separated
    # list (which can hold codes, Arabic/English names, or numeric ids), with
    # a substring check on the name, plus built-in Arabic/English keyword
    # fallbacks so the common statuses work out of the box.

    _AL_DELIVERED_KEYWORDS = ('تم التسليم', 'تسليم تام', 'سلمت', 'تسلیم',
                              'DELIVER', 'DTR')
    _AL_RETURNED_KEYWORDS = ('ارتجاع', 'إرجاع', 'مرتجع', 'راجع', 'مرتد',
                             'RETURN', 'RTRN', 'RTS')
    _AL_CANCELLED_KEYWORDS = ('ملغى', 'ملغي', 'إلغاء', 'الغاء', 'ملغاة',
                              'رفض', 'مرفوض', 'CANCEL', 'REJECT', 'RJCT')

    @staticmethod
    def _al_status_match(configured_csv, keywords, status_code,
                         status_name=None, status_id=None):
        """True if the status (by code / name / id) belongs to a family.

        1. Exact match (case-insensitive) of code, name, or id against the
           configured comma-separated list.
        2. Substring match of any configured token inside the name.
        3. Built-in keyword substring fallback on the name.
        """
        name_u = (str(status_name) if status_name else '').upper()
        exact = {str(x).strip().upper() for x in (status_code, status_name, status_id) if x}
        tokens = [t.strip() for t in (configured_csv or '').split(',') if t.strip()]
        for tok in tokens:
            tok_u = tok.upper()
            if tok_u in exact:
                return True
            if name_u and tok_u in name_u:
                return True
        for kw in keywords:
            if name_u and kw.upper() in name_u:
                return True
        return False

    def _is_delivered_code(self, status_code, status_name=None, status_id=None):
        return self._al_status_match(
            self.delivered_status_codes, self._AL_DELIVERED_KEYWORDS,
            status_code, status_name, status_id,
        )

    def _is_returned_code(self, status_code, status_name=None, status_id=None):
        return self._al_status_match(
            self.returned_status_codes, self._AL_RETURNED_KEYWORDS,
            status_code, status_name, status_id,
        )

    def _is_cancelled_code(self, status_code, status_name=None, status_id=None):
        return self._al_status_match(
            self.cancelled_status_codes, self._AL_CANCELLED_KEYWORDS,
            status_code, status_name, status_id,
        )
