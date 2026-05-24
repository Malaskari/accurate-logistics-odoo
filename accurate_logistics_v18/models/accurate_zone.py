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

    # ── Link to delivery companies ────────────────────────────────────────────
    delivery_company_ids = fields.Many2many(
        'accurate.delivery.company',
        'accurate_company_zone_rel',
        'zone_id', 'company_id',
        string='Delivery Companies',
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

    @api.constrains('api_id', 'is_subzone')
    def _check_api_id_unique(self):
        for rec in self:
            if not rec.api_id:
                continue
            duplicate = self.search([
                ('api_id', '=', rec.api_id),
                ('is_subzone', '=', rec.is_subzone),
                ('id', '!=', rec.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(
                    'A %s with API ID %d already exists: %s'
                    % ('sub-zone' if rec.is_subzone else 'zone', rec.api_id, duplicate.name)
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

    # ── Sync subzone ↔ company.subzone_ids relation ──────────────────────────
    # accurate.delivery.company has TWO M2M tables to accurate.zone:
    #   - zone_ids   → parent zones   (table accurate_company_zone_rel)
    #   - subzone_ids → sub-zones      (table accurate_company_subzone_rel)
    # The user-facing field on accurate.zone (`delivery_company_ids`) only
    # writes to the FIRST table. When a sub-zone is linked to a company we
    # must also mirror that link into the company's subzone_ids so the
    # shipment/wizard dropdowns find it.

    def _sync_subzone_link(self, companies):
        """Ensure each company has this sub-zone in its subzone_ids, and
        remove from any company that's no longer selected.
        """
        for rec in self:
            if not rec.is_subzone:
                continue
            # Add to currently linked companies
            for company in companies:
                company.write({'subzone_ids': [(4, rec.id)]})
            # Remove from companies that previously had this sub-zone
            # but are no longer in delivery_company_ids
            stale = self.env['accurate.delivery.company'].search([
                ('subzone_ids', 'in', rec.id),
                ('id', 'not in', companies.ids),
            ])
            for company in stale:
                company.write({'subzone_ids': [(3, rec.id)]})

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.is_subzone and rec.delivery_company_ids:
                rec._sync_subzone_link(rec.delivery_company_ids)
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'delivery_company_ids' in vals or 'is_subzone' in vals:
            for rec in self:
                if rec.is_subzone:
                    rec._sync_subzone_link(rec.delivery_company_ids)
        return res

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

        # Pick a delivery company that has API credentials
        company = self.delivery_company_ids.filtered(
            lambda c: c.api_username and c.api_password
        )[:1]
        if not company:
            raise UserError(
                'This zone is not linked to any Delivery Company with API credentials.\n'
                'Link this zone to a Delivery Company that has its API configured.'
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
            existing = self.search(
                [('api_id', '=', z_id), ('is_subzone', '=', True)], limit=1
            )
            vals = {
                'api_id': z_id,
                'name': z_name,
                'is_subzone': True,
                'parent_id': self.id,
            }
            if existing:
                existing.write(vals)
                synced_ids.append(existing.id)
            else:
                rec = self.create(vals)
                synced_ids.append(rec.id)

        # Link the new sub-zones to the same company that owns the parent
        if synced_ids:
            company.write({'subzone_ids': [(4, sid) for sid in synced_ids]})

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
        """Create-or-update zone records; optionally link to a company."""
        count = 0
        created_ids = []
        for z in zones:
            z_id = z.get('id')
            z_name = z.get('name', '')
            if not z_id:
                continue
            existing = self.search(
                [('api_id', '=', z_id), ('is_subzone', '=', is_subzone)], limit=1
            )
            vals = {'api_id': z_id, 'name': z_name, 'is_subzone': is_subzone}
            if existing:
                existing.write(vals)
                created_ids.append(existing.id)
            else:
                rec = self.create(vals)
                created_ids.append(rec.id)
            count += 1
        if company and created_ids:
            company.write({'zone_ids': [(4, zid) for zid in created_ids]})
        return count

    def _upsert_subzones(self, subzones, parent, company=None):
        """Create-or-update sub-zone records under *parent*."""
        count = 0
        created_ids = []
        for z in subzones:
            z_id = z.get('id')
            z_name = z.get('name', '')
            if not z_id:
                continue
            existing = self.search(
                [('api_id', '=', z_id), ('is_subzone', '=', True)], limit=1
            )
            vals = {'api_id': z_id, 'name': z_name, 'is_subzone': True, 'parent_id': parent.id}
            if existing:
                existing.write(vals)
                created_ids.append(existing.id)
            else:
                rec = self.create(vals)
                created_ids.append(rec.id)
            count += 1
        if company and created_ids:
            company.write({'subzone_ids': [(4, zid) for zid in created_ids]})
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
