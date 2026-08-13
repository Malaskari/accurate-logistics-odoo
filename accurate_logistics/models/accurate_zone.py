from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccurateZone(models.Model):
    _name = 'accurate.zone'
    _description = 'Accurate Logistics Zone'
    _inherit = []
    _rec_name = 'name'
    _order = 'parent_id, name'

    api_id = fields.Integer('API ID', index=True, copy=False)
    name = fields.Char('Name', required=True)
    is_subzone = fields.Boolean('Is Sub-zone', default=False, index=True)
    in_price_list = fields.Boolean(
        'In Price List',
        default=True, index=True,
        help='False if Validate Price List on the Delivery Company found '
             'this sub-zone has no price entry. Excluded from dropdowns when '
             'False so the salesperson cannot pick an unsupported destination.',
    )
    price_list_validated_at = fields.Datetime(
        'Price List Validated At',
        readonly=True, copy=False,
    )
    parent_id = fields.Many2one(
        'accurate.zone',
        string='Parent Zone',
        domain=[('is_subzone', '=', False)],
        ondelete='cascade',
        index=True,
    )
    child_ids = fields.One2many('accurate.zone', 'parent_id', string='Sub-zones')
    child_count = fields.Integer('Sub-zone Count', compute='_compute_child_count')

    # ── Owning delivery company ───────────────────────────────────────────────
    # Each delivery company keeps its OWN zone catalog: couriers (and even
    # separate tenants of the same courier) have independent id-spaces, so
    # zone api_id 1 means a different city for each of them. Sharing one row
    # between companies would rename/re-parent one company's zones when
    # another syncs.
    company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company',
        required=True,
        ondelete='cascade',
        index=True,
    )

    # ── Link to shipping services ─────────────────────────────────────────────
    # In Accurate Logistics, the price list (which zones+subzones are
    # available) is tied to the Shipping Service, not just the company.
    # This M2M lets you record which services this zone belongs to.
    # Currently dropdowns still filter by company; future API sync will
    # populate this per-service automatically.
    service_ids = fields.Many2many(
        'accurate.service',
        'accurate_service_zone_rel',
        'zone_id', 'service_id',
        string='Shipping Services',
        help='Which shipping services this zone belongs to. In Accurate '
             'Logistics, the available zones differ per service price list.',
    )

    @api.constrains('api_id', 'is_subzone', 'company_id')
    def _check_api_id_unique(self):
        """API ids are unique WITHIN a delivery company only — two companies
        legitimately use the same id for different zones."""
        for rec in self:
            if not rec.api_id:
                continue
            duplicate = self.search([
                ('api_id', '=', rec.api_id),
                ('is_subzone', '=', rec.is_subzone),
                ('company_id', '=', rec.company_id.id),
                ('id', '!=', rec.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(
                    'A %s with API ID %d already exists for %s: %s'
                    % ('sub-zone' if rec.is_subzone else 'zone', rec.api_id,
                       rec.company_id.name or '?', duplicate.name)
                )

    @api.constrains('is_subzone', 'parent_id')
    def _check_subzone_has_parent(self):
        for rec in self:
            if rec.is_subzone and not rec.parent_id:
                raise ValidationError(
                    'Sub-zone "%s" must have a Parent Zone.\n'
                    'يجب تحديد المنطقة الرئيسية للمنطقة الفرعية "%s".'
                    % (rec.name or '', rec.name or '')
                )

    # The company's zone_ids / subzone_ids are One2many views over company_id,
    # so there is no link table left to keep in sync — a zone simply belongs to
    # the delivery company that synced it.

    @api.depends('child_ids')
    def _compute_child_count(self):
        for rec in self:
            rec.child_count = len(rec.child_ids)

    def action_view_subzones(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Sub-zones of %s' % self.name,
            'res_model': 'accurate.zone',
            'view_mode': 'list,form',
            'domain': [('parent_id', '=', self.id)],
            'context': {'default_parent_id': self.id, 'default_is_subzone': True},
        }

    # ── Per-zone sub-zone sync ────────────────────────────────────────────────

    def action_sync_my_subzones(self):
        """Fetch and link sub-zones for THIS zone only.

        Uses any delivery company linked to this zone for API credentials.
        """
        self.ensure_one()
        from odoo.exceptions import UserError

        if self.is_subzone:
            raise UserError('Sub-zones cannot have their own sub-zones.')
        if not self.api_id:
            raise UserError('This zone has no API ID. Sync zones from the API first.')

        # The owning delivery company provides the API credentials.
        company = self.company_id
        if not (company and company.api_username and company.api_password):
            raise UserError(
                'This zone\'s Delivery Company has no API credentials.\n'
                'Configure the API on %s first.' % (company.name or 'the delivery company')
            )

        try:
            subzones = company._al_list_zones(filter_input={'parentId': self.api_id})
        except Exception as exc:
            raise UserError('API call failed: %s' % exc)

        if not subzones:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No Sub-zones',
                    'message': 'Zone "%s" has no sub-zones in the API.' % self.name,
                    'type': 'warning',
                    'sticky': False,
                },
            }

        synced_ids = []
        for z in subzones:
            z_id = z.get('id')
            z_name = z.get('name', '')
            if not z_id:
                continue
            existing = self.search([
                ('api_id', '=', z_id),
                ('is_subzone', '=', True),
                ('company_id', '=', company.id),
            ], limit=1)
            vals = {
                'api_id': z_id,
                'name': z_name,
                'is_subzone': True,
                'parent_id': self.id,
                'company_id': company.id,
            }
            if existing:
                existing.write(vals)
                synced_ids.append(existing.id)
            else:
                rec = self.create(vals)
                synced_ids.append(rec.id)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sub-zones Synced',
                'message': 'Synced %d sub-zones for "%s".' % (len(synced_ids), self.name),
                'type': 'success',
                'sticky': False,
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _upsert_zones(self, zones, is_subzone=False, company=None):
        """Create-or-update the zone records OWNED BY *company*.

        Scoped by company: every delivery company keeps its own catalog, so an
        api_id already used by another company is never touched."""
        if not company:
            raise ValueError('A delivery company is required to sync zones.')
        count = 0
        for z in zones:
            z_id = z.get('id')
            z_name = z.get('name', '')
            if not z_id:
                continue
            existing = self.search([
                ('api_id', '=', z_id),
                ('is_subzone', '=', is_subzone),
                ('company_id', '=', company.id),
            ], limit=1)
            vals = {
                'api_id': z_id, 'name': z_name, 'is_subzone': is_subzone,
                'company_id': company.id,
            }
            if existing:
                existing.write(vals)
            else:
                self.create(vals)
            count += 1
        return count

    def _upsert_subzones(self, subzones, parent, company=None):
        """Create-or-update sub-zone records under *parent*, owned by the same
        company as the parent zone."""
        company = company or parent.company_id
        if not company:
            raise ValueError('A delivery company is required to sync sub-zones.')
        count = 0
        for z in subzones:
            z_id = z.get('id')
            z_name = z.get('name', '')
            if not z_id:
                continue
            existing = self.search([
                ('api_id', '=', z_id),
                ('is_subzone', '=', True),
                ('company_id', '=', company.id),
            ], limit=1)
            vals = {
                'api_id': z_id, 'name': z_name, 'is_subzone': True,
                'parent_id': parent.id, 'company_id': company.id,
            }
            if existing:
                existing.write(vals)
            else:
                self.create(vals)
            count += 1
        return count

    @staticmethod
    def _notify(title, message):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': 'success',
                'sticky': False,
            },
        }
