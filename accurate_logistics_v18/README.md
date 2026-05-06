# Accurate Logistics Integration for Odoo 18

End-to-end integration between **Odoo 18 Community** and the
[Accurate Logistics](https://accuratess.com) GraphQL API
(supports Bohairat, Marsool and other Accurate-powered instances).

![icon](static/description/icon.png)

## What it does

1. Add a **recipient zone, sub-zone and delivery company** on a Sale Order.
2. **Confirm the SO** — fields auto-copy to the Delivery Order.
3. **Validate the Delivery Order** — a shipment is created in Accurate Logistics
   over the GraphQL API.
4. Accurate calls the Odoo **webhook** every time the status changes.
5. When the status reaches **Delivered**, Odoo automatically:
   - Creates and posts the customer invoice
   - Registers a COD payment in the Delivery Company's journal
   - Reconciles the payment with the invoice

## Features

- **Per-company API credentials** — each Delivery Company stores its own URL,
  username, password, branch ID and bearer token (auto-refreshed every hour).
- **Auto-discover branches** — *Test Connection* probes branch IDs 1-20.
- **Sync zones, sub-zones and shipping services** with one click
  (parallel API calls + background thread).
- **Calculate shipping fees** before dispatching (wizard).
- **Filter zones by company** across Sale Order, Delivery Order, Shipment
  and the wizard.
- **Classification fields** on the Sale Order so the sales team can pick
  Shipment Type / Payment Type / Price Type / Openable per order.
- **Full Arabic UI** — view labels, field labels, selection values, menus
  and action names all translated.
- **Manual "Mark as Delivered (Test)" button** — fires the same flow the
  webhook would, useful for end-to-end testing without waiting for the courier.
- **Cron job** as a fallback for missed webhook calls.

## Installation

1. Copy this directory to your Odoo `addons` folder:
   ```bash
   cp -r accurate_logistics /mnt/extra-addons/
   ```
2. Restart Odoo and update the apps list.
3. Install the **Accurate Logistics Integration** module from Apps.
4. Open *Accurate Logistics → Configuration → Delivery Companies*, create
   a company, fill in the API URL + credentials, click **Test Connection**.
5. Click **Sync Zones & Sub-zones** to import the courier's zone tree.

## Webhook setup

Configure the following URL in your Accurate Logistics dashboard
(or ask their support to set it):

```
https://<your-odoo-domain>/accurate/webhook?secret=<your-secret>
```

Generate the secret in **Settings → Technical → System Parameters** under the
key `accurate_logistics.webhook_secret`.

## Compatibility

- Odoo **18.0** Community (primary target)
- For Odoo 19, use the sister repo / `accurate_logistics` folder.

## License

LGPL-3
