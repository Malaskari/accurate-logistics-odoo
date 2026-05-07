from odoo import api, fields, models
from odoo.exceptions import UserError


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

        # 1. Call the Accurate API to cancel
        try:
            ship.delivery_company_id._al_cancel_shipments(
                [ship.api_id], cancel=True,
            )
        except Exception as exc:
            raise UserError(
                'Cancel call failed on Accurate Logistics:\n%s\n\n'
                'فشل إلغاء الشحنة على أكيوريت لوجيستكس.' % exc
            )

        # 2. Save the reason on the shipment + run the local cancellation flow
        ship.write({
            'cancellation_reason_id': self.reason_id.id,
            'cancellation_notes': self.notes or False,
        })
        ship.message_post(
            body='<b>Cancelled via wizard.</b><br/>Reason: %s<br/>Notes: %s' % (
                self.reason_id.name or self.reason_id.code or '—',
                self.notes or '—',
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
