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

        # ── 1. Parse JSON body ────────────────────────────────────────────────
        try:
            raw = request.httprequest.data
            payload = json.loads(raw) if raw else {}
        except (ValueError, TypeError) as exc:
            _logger.error('Accurate webhook: invalid JSON body – %s', exc)
            return _json_response({'error': 'Invalid JSON body'}, status=400)

        if not payload:
            # Some platforms send form data instead of JSON
            payload = dict(kwargs)

        # Everything downstream calls payload.get(...). A JSON array (batched
        # events) or a bare scalar would raise AttributeError outside the
        # try/except below and turn every callback into a 500, so reject the
        # shape here with a response the sender can act on.
        if not isinstance(payload, dict):
            _logger.error(
                'Accurate webhook: expected a JSON object, got %s.',
                type(payload).__name__,
            )
            return _json_response(
                {'error': 'Expected a JSON object'}, status=400)

        _logger.info('Accurate webhook received: %s', json.dumps(payload)[:500])

        # auth='none' leaves request.env.uid = None, so .sudo() alone would
        # give an empty res.users (env.user.lang etc. would raise
        # "Expected singleton: res.users()"). Bind a real superuser env.
        env = request.env(user=SUPERUSER_ID)

        # ── 2. Validate secret token ──────────────────────────────────────────
        # Per-company: the secret must match the Delivery Company that owns
        # the shipment in this payload (global secret kept as fallback).
        received = (
            request.httprequest.args.get('secret')
            or request.httprequest.headers.get('X-Webhook-Secret')
            or request.httprequest.headers.get('Authorization', '').replace('Bearer ', '')
        )
        if not env['accurate.shipment']._webhook_secret_valid(received, payload):
            # _webhook_secret_valid already logged WHICH branch rejected and
            # which company owns the secret — don't flatten that to a
            # context-free warning here.
            return _json_response({'error': 'Unauthorized'}, status=401)

        # ── 3. Process via model ──────────────────────────────────────────────
        try:
            result = env['accurate.shipment']._process_webhook(payload)
        except Exception as exc:
            _logger.exception('Accurate webhook: processing error – %s', exc)
            return _json_response({'error': str(exc)}, status=500)

        # A result carrying 'error' means the payload was understood but could
        # NOT be applied (unknown shipment code, no code at all, …). This used
        # to return 200, so the courier's dashboard reported every dropped
        # event as delivered successfully — which is how ~9,900 lost callbacks
        # went unnoticed for seven weeks. Answer with 422 so the sender can
        # retry or alert.
        if isinstance(result, dict) and result.get('error'):
            # 404 when the shipment simply isn't in Odoo (a PERMANENT outcome
            # — retrying can never succeed, so don't invite an endless retry
            # loop); 422 for anything else. _process_webhook already logged
            # the reason, so don't log it twice.
            status = 404 if result.pop('not_found', False) else 422
            return _json_response(result, status=status)

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
