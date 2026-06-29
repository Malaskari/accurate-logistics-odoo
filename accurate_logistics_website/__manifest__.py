{
    'name': 'Accurate Logistics — Website Checkout',
    'version': '18.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': 'Let eCommerce customers pick the Accurate zone / sub-zone and pay '
               'the courier delivery fee at website checkout.',
    'description': """
Accurate Logistics — Website Checkout
=====================================
Bridge between **accurate_logistics** and Odoo **eCommerce** (website_sale).

Adds an "Accurate Logistics" delivery method to the online checkout. The customer:
1. Picks the Accurate Logistics delivery method (tied to one fixed delivery company).
2. Chooses the Recipient Zone, then Sub-zone (cascading dropdowns).
3. Sees the courier delivery fee, fetched live from the Accurate API, added as a
   charged delivery line on the order.
4. Pays and confirms — the existing accurate_logistics flow then auto-creates the
   shipment from the chosen zone / sub-zone.

The core accurate_logistics module stays backend-only; install this bridge only on
sites that sell online.
""",
    'author': '',
    'depends': ['accurate_logistics_v18', 'website_sale', 'delivery'],
    'data': [
        'views/delivery_carrier_views.xml',
        'views/checkout_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'accurate_logistics_website/static/src/js/accurate_checkout.js',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
