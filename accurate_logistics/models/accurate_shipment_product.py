from odoo import api, fields, models


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

    # ── Shared product identity (Odoo ⇄ Accurate) ─────────────────────────────
    # The join key between the two systems is the product's Internal Reference
    # (product.default_code): the user maintains Accurate's product list with
    # the same codes (Product.code on the Accurate API).
    product_id = fields.Many2one(
        'product.product', string='Product', index=True,
        help='The Odoo product this line ships. Its Internal Reference must '
             'match the product code in the Accurate product list.',
    )
    default_code = fields.Char(
        related='product_id.default_code', string='SKU', store=True,
    )
    api_product_id = fields.Integer(
        'Accurate Product ID', readonly=True, copy=False,
        help="Accurate's internal id for this product (resolved by matching "
             "the SKU against the courier's product list).",
    )

    # ── Partial-delivery outcome (filled from the API) ────────────────────────
    delivered_qty = fields.Float('Delivered', readonly=True, copy=False)
    returned_qty = fields.Float('Returned', readonly=True, copy=False)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        for line in self:
            if line.product_id and not line.name:
                sku = line.product_id.default_code
                line.name = ('[%s] %s' % (sku, line.product_id.name)
                             if sku else line.product_id.name)
            if line.product_id and not line.price:
                line.price = line.product_id.lst_price
