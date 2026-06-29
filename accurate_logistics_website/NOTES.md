# accurate_logistics_website — setup & verification

Bridge that lets eCommerce customers pick the Accurate Logistics Zone / Sub-zone and
pay the courier delivery fee at checkout. The shipment is still auto-created by
`accurate_logistics` on order confirmation.

## One-time admin setup
1. Install **Accurate Logistics — Website Checkout** (pulls in `website_sale` + `delivery`).
2. Make sure the online shop already works (products published, a payment method;
   add a Cash-on-Delivery method if you want COD).
3. Make sure Zones / Services are synced (they need an `api_id`).
4. Inventory → Configuration → **Delivery Methods** → New:
   - Provider = **Accurate Logistics**
   - **Accurate Delivery Company** = the one fixed company for the site (e.g. Alyamama)
   - set the Delivery Product / taxes, **Website Published = on**
   - keep **Integration Level = "Get Rate"** (we create the shipment ourselves).

## How it works
- Checkout shows the "Accurate Logistics" method with Zone + Sub-zone dropdowns
  (`views/checkout_templates.xml`, `static/src/js/accurate_checkout.js`).
- Choosing a Sub-zone POSTs to `/accurate/website/set_recipient`
  (`controllers/main.py`), which saves the zone/sub-zone, re-quotes via
  `delivery.carrier.accurate_rate_shipment` → `accurate.delivery.company._al_calculate_fees`,
  and updates the charged delivery line.
- Selecting the method stamps the Accurate company + service onto the order
  (`models/sale_order.py: set_delivery_line`).
- On confirmation, the existing `accurate_logistics` `_action_confirm` auto-creates
  the shipment from the chosen zone/sub-zone.
- The payment step is blocked until a Zone + Sub-zone are chosen
  (`controllers/main.py: _get_shop_payment_errors`).

## ⚠️ Must verify on a LOCAL Odoo 18 (built/tested against Odoo 19 source)
The checkout markup/JS differs between Odoo versions. Verify on a dev Odoo 18:
1. `website_sale.delivery_method` template id + structure — the xpath anchor
   `//label[@name='o_delivery_method_label']/../..` and the `dm` / `order` vars.
2. The frontend wrapper selector `.oe_website_sale` in `accurate_checkout.js`
   (delegated events rely on it existing at page load).
3. `WebsiteSale._get_shop_payment_errors(order)` exists in this version.
4. End-to-end: add product → checkout → Accurate method → Zone → Sub-zone →
   fee appears as a charged line → pay → order confirms → shipment auto-created →
   delivery slip shows the same fee. Negative: blocked at payment with no sub-zone.

Do NOT test on production. Use a local/staging Odoo 18.
