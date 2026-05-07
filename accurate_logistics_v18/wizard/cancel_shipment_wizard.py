import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CancelShipmentWizard(models.TransientModel):
    _name = 'accurate.cancel.shipment.wizard'
    _description = 'Cancel Accurate Shipment'

    shipment_id = fields.Many2one(
        'accurate.shipment',
        string='Shipment',
        required=True,
        ondelete='cascade',
    )
    delivery_company_id = fields.Many2one(
        related='shipment_id.delivery_company_id',
        string='Delivery Company',
        readonly=True,
    )
    reason_id = fields.Many2one(
        'accurate.cancellation.reason',
        string='Cancellation Reason',
        required=True,
        domain=[('active', '=', True)],
    )
    notes = fields.Text(
        'Notes',
        help='Optional free-form notes saved on the shipment for record-keeping.',
    )
    force_local_cancel = fields.Boolean(
        'Cancel locally only',
        default=False,
        help='Skip the Accurate API call and just mark the shipment cancelled in Odoo. '
             'Use this when the courier has already moved past the cancellable phase '
             '(e.g. shipment is out for delivery) but you still want Odoo to reflect '
             'the cancellation. The actual courier-side state stays whatever Accurate says.',
    )

    def action_cancel_now(self):
        self.ensure_one()
        ship = self.shipment_id
        if not ship:
            raise UserError('No shipment to cancel.')
        if ship.state in ('cancelled', 'returned'):
            raise UserError(
                'Shipment is already in state "%s" — nothing to cancel.' % ship.state
            )
        if not ship.api_id:
            raise UserError(
                'Shipment has no API ID — it may not have been sent to Accurate '
                'Logistics yet. Reset to draft and delete it instead.'
            )

        api_cancelled = False
        if not self.force_local_cancel:
            # Pre-check: ask Accurate if the shipment is still cancellable
            try:
                api_data = ship.delivery_company_id._al_get_shipment(api_id=ship.api_id)
            except Exception as exc:
                _logger.warning('Accurate: pre-check fetch failed for %s: %s', ship.name, exc)
                api_data = {}
            if api_data and api_data.get('cancellable') is False:
                status = (api_data.get('status') or {}).get('name') or '—'
                raise UserError(
                    'Accurate Logistics will not allow cancelling this shipment.\n'
                    'Current courier status: %s\n\n'
                    'The shipment is past the cancellable phase (already picked up '
                    'or out for delivery). Two options:\n'
                    '  1. Wait for the courier to deliver / return it.\n'
                    '  2. Re-open this wizard and check "Cancel locally only" — '
                    'this marks Odoo as cancelled but leaves the courier flow alone.\n\n'
                    'لن تسمح أكيوريت لوجيستكس بإلغاء هذه الشحنة. الحالة الحالية: %s\n'
                    'يمكنك تحديد "إلغاء محلياً فقط" لتجاهل الواجهة البرمجية.'
                    % (status, status)
                )

            # 1. Call the Accurate API to cancel
            try:
                ship.delivery_company_id._al_cancel_shipments(
                    [ship.api_id], cancel=True,
                )
                api_cancelled = True
            except Exception as exc:
                err = str(exc)
                # If API rejects with the "cannot update status" error, suggest the
                # local-only fallback rather than blowing up.
                if 'لا يمكن تحديث الحالة' in err or 'cannot update' in err.lower():
                    raise UserError(
                        'Accurate Logistics rejected the cancel call:\n%s\n\n'
                        'The shipment is past the cancellable phase. To cancel '
                        'in Odoo only, re-open this wizard and check '
                        '"Cancel locally only".\n\n'
                        'فشل إلغاء الشحنة. الشحنة تجاوزت مرحلة الإلغاء. '
                        'يمكنك تحديد "إلغاء محلياً فقط" للمتابعة.' % err
                    )
                raise UserError(
                    'Cancel call failed on Accurate Logistics:\n%s\n\n'
                    'فشل إلغاء الشحنة على أكيوريت لوجيستكس.' % err
                )

        # 2. Save the reason on the shipment + run the local cancellation flow
        ship.write({
            'cancellation_reason_id': self.reason_id.id,
            'cancellation_notes': self.notes or False,
        })
        ship.message_post(
            body='<b>Cancelled via wizard.</b><br/>Reason: %s<br/>Notes: %s<br/>API cancelled: %s' % (
                self.reason_id.name or self.reason_id.code or '—',
                self.notes or '—',
                'yes' if api_cancelled else 'no — local only',
            ),
        )
        ship._on_cancelled()

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'accurate.shipment',
            'res_id': ship.id,
            'view_mode': 'form',
            'target': 'current',
        }
