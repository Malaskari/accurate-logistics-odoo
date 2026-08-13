"""Give every shipping service and cancellation reason an owning company.

Both models already carried an optional `company_id`; this version makes it
required (each merchant account exposes its own services / reason list, so the
same api_id means something different per company). Rows created before the
field existed may still be orphaned — attach them to the oldest delivery
company so the NOT NULL constraint can be applied and they stay usable
(records with no company are invisible anyway: every dropdown filters by it).

Idempotent: a no-op once nothing is orphaned.
"""

import logging

_logger = logging.getLogger(__name__)

MODELS = [
    ('accurate_service', 'shipping services'),
    ('accurate_cancellation_reason', 'cancellation reasons'),
]


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", (table,))
    return cr.fetchone()[0] is not None


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT id FROM accurate_delivery_company ORDER BY id LIMIT 1")
    row = cr.fetchone()
    if not row:
        return  # no companies yet — nothing can be orphaned
    fallback_company = row[0]

    for table, label in MODELS:
        if not _table_exists(cr, table) or not _column_exists(cr, table, 'company_id'):
            continue
        cr.execute(
            "UPDATE %s SET company_id = %%s WHERE company_id IS NULL" % table,
            (fallback_company,),
        )
        if cr.rowcount:
            _logger.warning(
                "%s %s had no delivery company — assigned to company %s",
                cr.rowcount, label, fallback_company,
            )

    # Drop duplicates that the new per-company uniqueness would reject: keep
    # the lowest id for each (company_id, api_id) pair.
    for table, label in MODELS:
        if not _table_exists(cr, table) or not _column_exists(cr, table, 'api_id'):
            continue
        cr.execute("""
            DELETE FROM {table} a
             USING {table} b
             WHERE a.api_id = b.api_id
               AND a.company_id = b.company_id
               AND a.api_id IS NOT NULL AND a.api_id != 0
               AND a.id > b.id
        """.format(table=table))
        if cr.rowcount:
            _logger.warning("Removed %s duplicate %s", cr.rowcount, label)
