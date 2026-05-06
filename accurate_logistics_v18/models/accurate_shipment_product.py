from odoo import fields, models


class AccurateShipmentProduct(models.Model):
    _name = 'accurate.shipment.product'
    _description = 'Accurate Shipment Product Line'
    _order = 'sequence, id'

    shipment_id = fields.Many2one(
        'accurate.shipment',
        string='Shipment',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer('Sequence', default=10)
    name = fields.Char('Product Name', required=True)
    quantity = fields.Float('Quantity', default=1.0)
    price = fields.Float('Unit Price', digits=(16, 2))
