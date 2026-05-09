from odoo import api, fields, models
from odoo.exceptions import UserError


class AccurateCalculateFeesWizard(models.TransientModel):
    _name = 'accurate.calculate.fees.wizard'
    _description = 'Calculate Accurate Logistics Shipping Fees'

    shipment_id = fields.Many2one('accurate.shipment', string='Shipment', readonly=True)
    delivery_company_id = fields.Many2one(
        'accurate.delivery.company',
        string='Delivery Company',
        required=True,
        help='The company whose API credentials are used to calculate fees.',
    )

    # ── Inputs ────────────────────────────────────────────────────────────────

    recipient_zone_id = fields.Many2one(
        'accurate.zone', string='Recipient Zone', required=True,
        domain="[('is_subzone', '=', False), ('delivery_company_ids', 'in', [delivery_company_id])] if delivery_company_id else [('id', '=', 0)]",
    )
    recipient_subzone_id = fields.Many2one(
        'accurate.zone', string='Recipient Sub-zone', required=True,
        domain="[('is_subzone', '=', True), ('parent_id', '=', recipient_zone_id), ('in_price_list', '=', True)] if recipient_zone_id else [('id', '=', 0)]",
    )
    service_id = fields.Many2one('accurate.service', string='Shipping Service', required=True)
    price = fields.Float('Declared Value', required=True, digits=(16, 2))
    weight = fields.Float('Weight (kg)', required=True, digits=(10, 3))
    payment_type_code = fields.Selection(
        [
            ('COLC', 'COD – Collect on Delivery'),
            ('CRDT', 'Credit / Postpaid'),
            ('CASH', 'Cash – Already Paid'),
        ],
        string='Payment Type', default='COLC',
    )
    price_type_code = fields.Selection(
        [
            ('EXCLD', 'Shipping Fee Excluded from Price'),
            ('INCLD', 'Shipping Fee Included in Price'),
        ],
        string='Price Type', default='EXCLD',
    )

    # ── Results (read-only) ────────────────────────────────────────────────────

    result_computed = fields.Boolean('Result Computed', default=False)
    result_amount = fields.Float('Amount', readonly=True, digits=(16, 2))
    result_delivery = fields.Float('Delivery Fees', readonly=True, digits=(16, 2))
    result_weight = fields.Float('Weight Fees', readonly=True, digits=(16, 2))
    result_collection = fields.Float('Collection Fees', readonly=True, digits=(16, 2))
    result_post = fields.Float('Post Fees', readonly=True, digits=(16, 2))
    result_tax = fields.Float('Tax', readonly=True, digits=(16, 2))
    result_return = fields.Float('Return Fees', readonly=True, digits=(16, 2))
    result_total = fields.Float('Total', readonly=True, digits=(16, 2))

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_calculate(self):
        self.ensure_one()
        if not (self.recipient_zone_id.api_id and self.recipient_subzone_id.api_id):
            raise UserError(
                'Zone and Sub-zone must have valid API IDs. '
                'Please sync zones first from Configuration → Zones.'
            )
        if not self.service_id.api_id:
            raise UserError(
                'Shipping Service must have a valid API ID. '
                'Please sync services first from Configuration → Services.'
            )

        fee_input = {
            'price': self.price,
            'weight': self.weight,
            'serviceId': self.service_id.api_id,
            'recipientZoneId': self.recipient_zone_id.api_id,
            'recipientSubzoneId': self.recipient_subzone_id.api_id,
        }
        if self.payment_type_code:
            fee_input['paymentTypeCode'] = self.payment_type_code
        if self.price_type_code:
            fee_input['priceTypeCode'] = self.price_type_code

        if not self.delivery_company_id:
            raise UserError('Please select a Delivery Company.')
        fees = self.delivery_company_id._al_calculate_fees(fee_input)

        self.write({
            'result_computed': True,
            'result_amount': fees.get('amount', 0),
            'result_delivery': fees.get('delivery', 0),
            'result_weight': fees.get('weight', 0),
            'result_collection': fees.get('collection', 0),
            'result_post': fees.get('post', 0),
            'result_tax': fees.get('tax', 0),
            'result_return': fees.get('return', 0),
            'result_total': fees.get('total', 0),
        })

        # Keep wizard open to show results
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    def action_apply_to_shipment(self):
        """Copy fee results back to the linked shipment."""
        self.ensure_one()
        if not self.shipment_id:
            return {'type': 'ir.actions.act_window_close'}
        self.shipment_id.write({
            'fee_amount': self.result_amount,
            'fee_delivery': self.result_delivery,
            'fee_collection': self.result_collection,
            'fee_total': self.result_total,
            'service_id': self.service_id.id,
            'weight': self.weight,
            'price': self.price,
        })
        return {'type': 'ir.actions.act_window_close'}
