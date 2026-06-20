{
    'name': 'Accurate Logistics Integration',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Delivery',
    'summary': 'Accurate Logistics API — shipments, COD invoicing, webhook status sync',
    'description': """
Accurate Logistics Integration
================================
Full end-to-end integration with the Accurate Logistics GraphQL API.

Flow
----
1. Add recipient zone + sub-zone + delivery company on a Sale Order.
2. Confirm the SO — fields are auto-copied to the Delivery Order.
3. Validate the Delivery Order — shipment is created in Accurate Logistics.
4. Accurate Logistics calls the Odoo webhook whenever the status changes.
5. When the status reaches "Delivered", Odoo automatically:
   - Creates and validates the customer invoice.
   - Registers a payment in the Delivery Company's journal (COD).

Other features
--------------
- Sync zones and shipping services with a single click.
- Calculate shipping fees before dispatching.
- Manual shipment management independent of Sales / Stock.
- Scheduled status sync cron job.
- Supports Odoo 18 and 19 Community.
    """,
    'author': '',
    'depends': ['base', 'mail', 'sale_stock', 'account'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'data/ir_cron.xml',
        'views/res_config_settings_views.xml',
        'views/accurate_zone_views.xml',
        'views/accurate_service_views.xml',
        'views/accurate_cancellation_reason_views.xml',
        'views/accurate_delivery_company_views.xml',
        'views/accurate_shipment_views.xml',
        'wizard/calculate_fees_wizard_views.xml',
        'wizard/cancel_shipment_wizard_views.xml',
        'views/sale_order_views.xml',
        'views/stock_picking_views.xml',
        'views/report_delivery_slip.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
