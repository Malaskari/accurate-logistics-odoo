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

## Verified against Odoo 18 source (odoo/odoo @ 18.0)
- `website_sale.delivery_method` template exists; carrier var = `dm`, `order` in
  scope; price badge = `<span name="price">` inside a `col` inside the carrier row
  `<div class="row flex-column flex-md-row …">`. The xpath anchor is
  `//span[@name='price']/../..` (price span → col → row), insert after. ✓
- `WebsiteSale._get_shop_payment_errors(self, order)` exists and returns
  `(title, message)` tuples → the payment-step block works. ✓
- delivery_form is `<form id="o_delivery_form" class="o_delivery_form mb-4">`. ✓

## Still to confirm at runtime (not install-blocking)
- The frontend widget binds to `.oe_website_sale` (delegated events). Confirm that
  wrapper class is present on the checkout page; if not, switch the selector in
  `accurate_checkout.js` to `#wrap`.
- End-to-end smoke test: add product → checkout → Accurate method → Zone →
  Sub-zone → fee appears as a charged line → pay → order confirms → shipment
  auto-created → delivery slip shows the same fee. Negative: blocked at payment
  with no sub-zone chosen.
