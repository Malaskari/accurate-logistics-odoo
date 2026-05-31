import json
import logging

from odoo import SUPERUSER_ID, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AccurateWebhookController(http.Controller):
    """
    Receives status-update callbacks from Accurate Logistics.

    Configure this URL as the Callback URL in your Accurate Logistics account:

        https://<your-odoo-domain>/accurate/webhook?secret=<your-secret>

    The secret token is generated in Settings → Accurate Logistics.
    Accurate Logistics will POST a JSON payload to this endpoint every time a
    shipment status changes.
    """

    @http.route(
        '/accurate/webhook',
        type='http',
        auth='none',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def webhook(self, **kwargs):
        def _json_response(data, status=200):
            return request.make_response(
                json.dumps(data),
                headers=[('Content-Type', 'application/json')],
                status=status,
            )

        # ── 1. Validate secret token ──────────────────────────────────────────
        expected_secret = (
            request.env['ir.config_parameter']
            .sudo()
            .get_param('accurate_logistics.webhook_secret', '')
        )
        if expected_secret:
            received = (
                request.httprequest.args.get('secret')
                or request.httprequest.headers.get('X-Webhook-Secret')
                or request.httprequest.headers.get('Authorization', '').replace('Bearer ', '')
            )
            if received != expected_secret:
                _logger.warning('Accurate webhook: invalid secret token received.')
                return _json_response({'error': 'Unauthorized'}, status=401)

        # ── 2. Parse JSON body ────────────────────────────────────────────────
        try:
            raw = request.httprequest.data
            payload = json.loads(raw) if raw else {}
        except (ValueError, TypeError) as exc:
            _logger.error('Accurate webhook: invalid JSON body – %s', exc)
            return _json_response({'error': 'Invalid JSON body'}, status=400)

        if not payload:
            # Some platforms send form data instead of JSON
            payload = dict(kwargs)

        _logger.info('Accurate webhook received: %s', json.dumps(payload)[:500])

        # ── 3. Process via model ──────────────────────────────────────────────
        # auth='none' leaves request.env.uid = None, so .sudo() alone would
        # give an empty res.users (env.user.lang etc. would raise
        # "Expected singleton: res.users()"). Bind a real superuser env.
        try:
            env = request.env(user=SUPERUSER_ID)
            result = env['accurate.shipment']._process_webhook(payload)
        except Exception as exc:
            _logger.exception('Accurate webhook: processing error – %s', exc)
            return _json_response({'error': str(exc)}, status=500)

        return _json_response(result)

    @http.route(
        '/accurate/webhook/test',
        type='http',
        auth='user',
        methods=['GET'],
        csrf=False,
    )
    def webhook_test(self, **kwargs):
        """Quick health-check for the webhook endpoint (authenticated users only)."""
        base = request.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        secret = request.env['ir.config_parameter'].sudo().get_param(
            'accurate_logistics.webhook_secret', ''
        )
        url = '%s/accurate/webhook?secret=%s' % (base, secret) if secret else '%s/accurate/webhook' % base
        return request.make_response(
            json.dumps({'status': 'ok', 'webhook_url': url}),
            headers=[('Content-Type', 'application/json')],
        )
