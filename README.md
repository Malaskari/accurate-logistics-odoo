# Accurate Logistics Integration for Odoo

End-to-end integration between **Odoo Community** and the
[Accurate Logistics](https://accuratess.com) GraphQL API.
Supports Bohairat, Marsool and other Accurate-powered instances.

![icon](accurate_logistics/static/description/icon.png)

## Two builds in one repo

| Folder | Odoo version | Notes |
|---|---|---|
| [`accurate_logistics/`](./accurate_logistics) | **19.0** | Uses the new `<chatter/>` shorthand widget |
| [`accurate_logistics_v18/`](./accurate_logistics_v18) | **18.0** | Same code with the legacy `<div class="oe_chatter">` block |

Pick the folder that matches your Odoo version.

## Install

1. Pick the right folder for your Odoo (`accurate_logistics` for 19, `accurate_logistics_v18` for 18).
2. Copy it into your Odoo `addons` folder.
3. **Rename the folder to `accurate_logistics`** (the technical module name must match the folder name).
4. Restart Odoo and update the apps list.
5. Install **Accurate Logistics Integration** from Apps.
6. Open *Accurate Logistics → Configuration → Delivery Companies*, create a company, fill in API credentials, click **Test Connection**.
7. Click **Sync Zones & Sub-zones** to import the courier's zone tree.

## What it does

1. Add a **recipient zone, sub-zone and delivery company** on a Sale Order.
2. **Confirm the SO** — fields auto-copy to the Delivery Order.
3. **Validate the Delivery Order** — a shipment is created in Accurate Logistics over the GraphQL API.
4. Accurate calls the Odoo **webhook** every time the status changes.
5. When the status reaches **Delivered**, Odoo automatically:
   - Creates and posts the customer invoice
   - Registers a COD payment in the Delivery Company's journal
   - Reconciles the payment with the invoice

## Features

- **Per-company API credentials** — each Delivery Company stores its own URL, username, password, branch ID and bearer token (auto-refreshed every hour).
- **Auto-discover branches** — *Test Connection* probes branch IDs 1-20.
- **Sync zones, sub-zones and shipping services** with one click (parallel API calls in a background thread).
- **Calculate shipping fees** before dispatching (wizard).
- **Filter zones by company** across Sale Order, Delivery Order, Shipment and the wizard.
- **Classification fields** on the Sale Order so the sales team can pick Shipment Type / Payment Type / Price Type / Openable per order.
- **Full Arabic UI** — view labels, field labels, selection values, menus and action names all translated.
- **Manual "Mark as Delivered (Test)" button** — fires the same flow the webhook would, useful for end-to-end testing without waiting for the courier.
- **Cron job** as a fallback for missed webhook calls.

## Webhook setup

Configure the following URL in your Accurate Logistics dashboard
(or ask their support to set it):

```
https://<your-odoo-domain>/accurate/webhook?secret=<your-secret>
```

Generate the secret in **Settings → Technical → System Parameters** under the key `accurate_logistics.webhook_secret`.

## License

LGPL-3
