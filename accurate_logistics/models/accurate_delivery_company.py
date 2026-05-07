import logging
import threading
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
        default='DEL,DLV,DELIVERED',
        help='Comma-separated codes from Accurate that mean "Delivered" (triggers invoice+payment).',
    )
    default_service_id = fields.Many2one(
        'accurate.service',
        string='Default Shipping Service',
        help='Required by Accurate Logistics on every shipment. '
             'If left empty, the first synced service will be used.',
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

        # ── Sync services dropdown ───────────────────────────────────────
        services_synced = 0
        try:
            services = self._al_list_services() or []
            for s in services:
                s_id = s.get('id')
                s_name = s.get('name', '')
                if not s_id:
                    continue
                existing = Service.search([('api_id', '=', s_id)], limit=1)
                vals = {'api_id': s_id, 'name': s_name}
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
        self.write({'sync_in_progress': True, 'sync_progress': 'Starting…'})
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
                    self.write({
                        'sync_progress': 'Sub-zones: %d / %d zones processed (%d subs synced)' % (
                            processed, total, subzone_count,
                        ),
                    })
                    self.env.cr.commit()

        self.write({
            'sync_in_progress': False,
            'sync_progress': 'Done — %d zones, %d sub-zones' % (zone_count, subzone_count),
        })
        self.env.cr.commit()

    def action_clear_zones(self):
        """Remove all zone/subzone links from this company (does not delete zones)."""
        self.ensure_one()
        self.write({'zone_ids': [(5,)], 'subzone_ids': [(5,)]})

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

    # ── Helper ────────────────────────────────────────────────────────────────

    def _is_delivered_code(self, status_code):
        codes = [c.strip().upper() for c in (self.delivered_status_codes or '').split(',') if c.strip()]
        return (status_code or '').upper() in codes
