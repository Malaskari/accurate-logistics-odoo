import logging
from datetime import datetime, timedelta

import requests

from odoo import _, fields, models
from odoo.exceptions import UserError


# ── Error code → user-friendly bilingual message ────────────────────────────
# Each entry is (English, Arabic). When we recognize the API code, we show
# this instead of the raw GraphQL message.
_FRIENDLY_ERRORS = {
    'NO_PRICE_LIST_ENTRY': (
        "This sub-zone is not in your shipping company's price list. "
        "Please choose a different sub-zone, or contact your delivery "
        "company to add it.",
        "هذه المنطقة الفرعية غير موجودة في قائمة أسعار شركة الشحن. "
        "الرجاء اختيار منطقة فرعية أخرى، أو التواصل مع شركة الشحن "
        "لإضافة سعر لهذه المنطقة.",
    ),
    'UNAUTHENTICATED': (
        "Your shipping company login expired. Click 'Test Connection' "
        "on the Delivery Company form to log in again.",
        "انتهت صلاحية جلسة الدخول لشركة الشحن. اضغط على زر "
        "«اختبار الاتصال» في بطاقة شركة الشحن لإعادة تسجيل الدخول.",
    ),
}

# ── Field name translations for validation errors ──────────────────────────
_FIELD_LABELS = {
    'input.recipientAddress': ('Recipient Address', 'عنوان المستلم'),
    'input.recipientPhone':   ('Recipient Phone', 'هاتف المستلم'),
    'input.recipientMobile':  ('Recipient Mobile', 'جوال المستلم'),
    'input.recipientName':    ('Recipient Name', 'اسم المستلم'),
    'input.recipientZoneId':  ('Recipient Zone', 'منطقة المستلم'),
    'input.recipientSubzoneId': ('Recipient Sub-zone', 'منطقة المستلم الفرعية'),
    'input.serviceId':        ('Shipping Service', 'خدمة الشحن'),
    'input.weight':           ('Weight', 'الوزن'),
    'input.price':            ('Price', 'السعر'),
    'input.typeCode':         ('Shipment Type', 'نوع الشحنة'),
    'input.paymentTypeCode':  ('Payment Type', 'نوع الدفع'),
    'input.priceTypeCode':    ('Price Type', 'نوع السعر'),
    'input.openableCode':     ('Openable', 'قابلية الفتح'),
    'input.date':             ('Date', 'التاريخ'),
}


def _format_api_error(errors):
    """Turn a GraphQL errors list into a user-friendly bilingual message."""
    parts = []
    for err in errors:
        raw_msg = err.get('message', '') or 'Unknown error'
        ext = err.get('extensions') or {}
        code = ext.get('code')
        validation = ext.get('validation') or {}

        # 1. Known error codes — show our bilingual friendly text.
        if code and code in _FRIENDLY_ERRORS:
            en, ar = _FRIENDLY_ERRORS[code]
            parts.append('%s\n%s\n\n(%s)' % (en, ar, raw_msg))
            continue

        # 2. Field-level validation errors (typeCode, weight, etc).
        if validation:
            lines_en = ['Some required information is missing or invalid:']
            lines_ar = ['بعض الحقول المطلوبة ناقصة أو غير صحيحة:']
            for fname, errs in validation.items():
                en_label, ar_label = _FIELD_LABELS.get(
                    fname, (fname, fname)
                )
                detail = ' / '.join(errs) if isinstance(errs, list) else str(errs)
                lines_en.append('  • %s — %s' % (en_label, detail))
                lines_ar.append('  • %s — %s' % (ar_label, detail))
            parts.append('\n'.join(lines_en) + '\n\n' + '\n'.join(lines_ar))
            continue

        # 3. Unknown error — just show the raw message.
        prefix = '[%s] ' % code if code else ''
        parts.append('%s%s' % (prefix, raw_msg))

    return '\n\n──────────\n\n'.join(parts)

_logger = logging.getLogger(__name__)

_DEFAULT_URL = 'https://marsool.lg.accuratess.com:8001/graphql'


class AccurateApiMixin(models.AbstractModel):
    """
    Abstract mixin that provides Accurate Logistics GraphQL API access.

    The concrete model (accurate.delivery.company) must define these fields:
        api_url, api_username, api_password, ssl_verify,
        api_token, api_token_expiry
    """
    _name = 'accurate.api.mixin'
    _description = 'Accurate Logistics API Mixin'

    # ── Authentication ────────────────────────────────────────────────────────

    def _al_get_token(self):
        """Return a valid Bearer token, re-authenticating if expired or absent."""
        self.ensure_one()
        token = self.api_token
        expiry = self.api_token_expiry

        if token and expiry:
            try:
                if isinstance(expiry, str):
                    expiry = datetime.fromisoformat(expiry)
                if datetime.now() < expiry:
                    return token
            except (ValueError, TypeError):
                pass

        return self._al_login()

    def _al_login(self):
        """Authenticate with the API and cache the token on this company record."""
        self.ensure_one()
        username = self.api_username
        password = self.api_password
        if not username or not password:
            raise UserError(_(
                "Login details for shipping company \"%(name)s\" are not set.\n"
                "Open the delivery company form, then enter the Username and "
                "Password under 'API Connection'.\n\n"
                "بيانات الدخول لشركة الشحن «%(name)s» غير مكتملة. الرجاء فتح "
                "بطاقة شركة الشحن وإدخال اسم المستخدم وكلمة المرور في قسم "
                "«الاتصال بواجهة برمجة التطبيقات»."
            ) % {'name': self.name})

        mutation = """
            mutation Login($input: LoginInput!) {
                login(input: $input) {
                    token
                }
            }
        """
        variables = {
            'input': {
                'username': username,
                'password': password,
                'rememberMe': True,
            }
        }
        data = self._al_request(mutation, variables, authenticated=False)
        payload = data.get('login') or {}
        token = payload.get('token')
        if not token:
            raise UserError(_(
                "Could not log in to shipping company \"%(name)s\".\n"
                "Please check the username and password on the delivery "
                "company form, then try again.\n\n"
                "تعذر تسجيل الدخول إلى شركة الشحن «%(name)s». الرجاء التحقق "
                "من اسم المستخدم وكلمة المرور في بطاقة شركة الشحن وإعادة "
                "المحاولة."
            ) % {'name': self.name})

        ttl = payload.get('ttl', '')
        try:
            expiry = datetime.fromisoformat(str(ttl))
        except (ValueError, TypeError):
            expiry = datetime.now() + timedelta(hours=1)

        self.sudo().write({
            'api_token': token,
            'api_token_expiry': fields.Datetime.to_string(expiry),
        })
        _logger.info('Accurate Logistics: authenticated successfully for %s.', self.name)
        return token

    # ── HTTP / GraphQL transport ──────────────────────────────────────────────

    def _al_request(self, query, variables=None, authenticated=True):
        """Execute a GraphQL query/mutation. Returns the ``data`` dict."""
        self.ensure_one()
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer %s' % self._al_get_token()

        body = {'query': query}
        if variables:
            body['variables'] = variables

        url = self.api_url or _DEFAULT_URL
        ssl_verify = getattr(self, 'ssl_verify', True)

        try:
            resp = requests.post(url, headers=headers, json=body,
                                 verify=ssl_verify, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.SSLError as exc:
            raise UserError(_(
                "Could not connect to the shipping company securely.\n"
                "Ask your administrator to disable 'Verify SSL Certificate' "
                "on the Delivery Company form, or check the server certificate.\n\n"
                "تعذر الاتصال بشركة الشحن بشكل آمن. اطلب من المسؤول "
                "إلغاء «التحقق من شهادة SSL» في بطاقة شركة الشحن، أو "
                "التأكد من صلاحية شهادة الخادم.\n\n(%s)"
            ) % exc)
        except requests.exceptions.ConnectionError as exc:
            raise UserError(_(
                "Cannot reach the shipping company server. Please check "
                "your internet connection and try again.\n\n"
                "تعذر الوصول إلى خادم شركة الشحن. الرجاء التأكد من "
                "الاتصال بالإنترنت والمحاولة مرة أخرى.\n\n(%s)"
            ) % exc)
        except requests.exceptions.Timeout:
            raise UserError(_(
                "The request to the shipping company took too long. "
                "Please try again.\n\n"
                "استغرق الطلب من شركة الشحن وقتاً طويلاً. الرجاء "
                "المحاولة مرة أخرى."
            ))
        except requests.exceptions.RequestException as exc:
            raise UserError(_(
                "Request to the shipping company failed.\n\n"
                "فشل الطلب من شركة الشحن.\n\n(%s)"
            ) % exc)

        result = resp.json()
        if 'errors' in result:
            full = _format_api_error(result['errors'])
            _logger.error('Accurate Logistics API errors: %s', full)
            raise UserError(full)

        return result.get('data') or {}

    # ── Reusable API operations ───────────────────────────────────────────────

    def _al_list_zones(self, filter_input=None):
        query = """
            query ListZones($input: ListZonesFilterInput) {
                listZonesDropdown(input: $input) {
                    id
                    name
                }
            }
        """
        variables = {}
        if filter_input:
            variables['input'] = filter_input
        data = self._al_request(query, variables or None)
        return data.get('listZonesDropdown') or []

    def _al_list_services(self, filter_input=None):
        query = """
            query ListServices($input: ListShippingServicesFilterInput) {
                listShippingServicesDropdown(input: $input) {
                    id
                    name
                }
            }
        """
        variables = {}
        if filter_input:
            variables['input'] = filter_input
        data = self._al_request(query, variables or None)
        return data.get('listShippingServicesDropdown') or []

    def _al_list_payment_types(self):
        query = """
            query {
                listPaymentTypesDropdown {
                    id
                    name
                }
            }
        """
        data = self._al_request(query)
        return data.get('listPaymentTypesDropdown') or []

    def _al_list_shipment_types(self):
        query = """
            query {
                listShipmentTypesDropdown {
                    id
                    name
                }
            }
        """
        data = self._al_request(query)
        return data.get('listShipmentTypesDropdown') or []

    def _al_list_cancellation_reasons(self):
        """Pull the cancellation-reason master data from the Accurate API."""
        query = """
            query {
                listCancellationReasonsDropdown {
                    id
                    code
                    name
                }
            }
        """
        data = self._al_request(query)
        return data.get('listCancellationReasonsDropdown') or []

    def _al_cancel_shipments(self, shipment_api_ids, cancel=True):
        """Call the bulk cancelShipments mutation.

        Args:
            shipment_api_ids: list[int] — Accurate side ids of shipments to cancel.
            cancel: True to cancel, False to un-cancel (the API supports toggling).

        Returns the list of shipment dicts (id, code, status) the API echoed back.
        """
        if not shipment_api_ids:
            return []
        mutation = """
            mutation CancelShipments($input: CancelShipmentsInput!) {
                cancelShipments(input: $input) {
                    id
                    code
                    status { code name }
                }
            }
        """
        data = self._al_request(mutation, {
            'input': {
                'id': [int(x) for x in shipment_api_ids],
                'cancel': bool(cancel),
            }
        })
        return data.get('cancelShipments') or []

    def _al_calculate_fees(self, fee_input):
        """fee_input: dict matching CalculateShipmentFeesInput."""
        query = """
            query CalcFees($input: CalculateShipmentFeesInput!) {
                calculateShipmentFees(input: $input) {
                    amount
                    delivery
                    weight
                    collection
                    post
                    tax
                    return
                    total
                }
            }
        """
        data = self._al_request(query, {'input': fee_input})
        return data.get('calculateShipmentFees') or {}

    def _al_save_shipment(self, shipment_input):
        """shipment_input: dict matching ShipmentInput."""
        mutation = """
            mutation SaveShipment($input: ShipmentInput!) {
                saveShipment(input: $input) {
                    id
                    code
                    refNumber
                    date
                    deliveryDate
                    trackingUrl
                    notes
                    weight
                    piecesCount
                    price
                    amount
                    deliveryFees
                    collectionFees
                    totalAmount
                    recipientName
                    recipientPhone
                    recipientMobile
                    recipientAddress
                    status { code name }
                    type   { code name }
                    paymentType { code name }
                    priceType   { code name }
                    service { id name }
                    recipientZone    { id name }
                    recipientSubzone { id name }
                    senderZone    { id name }
                    senderSubzone { id name }
                }
            }
        """
        data = self._al_request(mutation, {'input': shipment_input})
        return data.get('saveShipment') or {}

    def _al_get_shipment(self, api_id=None, code=None):
        """Fetch a single shipment by API id or code."""
        query = """
            query GetShipment($id: Int, $code: String) {
                shipment(id: $id, code: $code) {
                    id
                    code
                    refNumber
                    date
                    deliveryDate
                    trackingUrl
                    notes
                    adminNotes
                    weight
                    piecesCount
                    returnPiecesCount
                    price
                    amount
                    deliveryFees
                    collectionFees
                    totalAmount
                    collected
                    cancellable
                    cancelled
                    recipientName
                    recipientPhone
                    recipientMobile
                    recipientAddress
                    recipientLatitude
                    recipientLongitude
                    senderName
                    senderPhone
                    senderMobile
                    senderAddress
                    status      { code name }
                    type        { code name }
                    paymentType { code name }
                    priceType   { code name }
                    service { id name }
                    recipientZone    { id name }
                    recipientSubzone { id name }
                    senderZone    { id name }
                    senderSubzone { id name }
                    lastDeliveryAgent { id name phone mobile }
                }
            }
        """
        variables = {}
        if api_id:
            variables['id'] = api_id
        if code:
            variables['code'] = code
        data = self._al_request(query, variables)
        return data.get('shipment') or {}

    def _al_list_shipments(self, first=20, page=1, filter_input=None):
        query = """
            query ListShipments(
                $input: ListShipmentsFilterInput
                $first: Int!
                $page: Int
            ) {
                listShipments(input: $input, first: $first, page: $page) {
                    data {
                        id
                        code
                        date
                        trackingUrl
                        totalAmount
                        recipientName
                        recipientMobile
                        recipientAddress
                        status { code name }
                    }
                    paginatorInfo {
                        total
                        currentPage
                        lastPage
                        hasMorePages
                    }
                }
            }
        """
        variables = {'first': first, 'page': page}
        if filter_input:
            variables['input'] = filter_input
        data = self._al_request(query, variables)
        return data.get('listShipments') or {}

    def _al_test_connection(self):
        """Clear cached token, re-authenticate, return True on success."""
        self.ensure_one()
        self.sudo().write({'api_token': '', 'api_token_expiry': False})
        self._al_login()
        return True
