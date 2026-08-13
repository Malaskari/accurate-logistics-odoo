"""Give every zone an owning delivery company (per-company zone catalogs).

Before this version `accurate.zone` was keyed by `api_id` alone and shared
between companies through two M2M tables. Different couriers / tenants have
independent id-spaces, so a second company's sync renamed and re-parented the
first company's zones instead of creating its own.

This script runs BEFORE the new required `company_id` column is enforced:

  1. every existing zone is given to the LOWEST-id company it was linked to
     (the company that created it),
  2. each additional company that shared a zone receives its own COPY —
     parents first, then sub-zones re-parented onto that company's copies,
  3. that company's live records (sale orders / transfers / shipments) are
     re-pointed to its copies, matched by api_id,
  4. the now-unused M2M tables are dropped.

Idempotent: re-running is a no-op once every zone has a company_id.
"""

import logging

_logger = logging.getLogger(__name__)

ZONE_REL = 'accurate_company_zone_rel'
SUBZONE_REL = 'accurate_company_subzone_rel'

# (table, [zone columns]) — live records that point at a zone.
LIVE_REFS = [
    ('sale_order', ['accurate_recipient_zone_id', 'accurate_recipient_subzone_id'],
     'accurate_delivery_company_id'),
    ('stock_picking', ['accurate_recipient_zone_id', 'accurate_recipient_subzone_id'],
     'accurate_delivery_company_id'),
    ('accurate_shipment', ['recipient_zone_id', 'recipient_subzone_id',
                           'sender_zone_id', 'sender_subzone_id'],
     'delivery_company_id'),
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
    if not _table_exists(cr, 'accurate_zone'):
        return

    # The column is created by Odoo when it loads the new model definition,
    # which happens AFTER this pre-migration — so add it ourselves (nullable),
    # fill it, and let Odoo apply NOT NULL afterwards.
    if not _column_exists(cr, 'accurate_zone', 'company_id'):
        cr.execute("ALTER TABLE accurate_zone ADD COLUMN company_id INTEGER")
        _logger.info("accurate_zone.company_id column added")

    cr.execute("SELECT count(*) FROM accurate_zone WHERE company_id IS NULL")
    todo = cr.fetchone()[0]
    if not todo:
        _logger.info("Zone ownership already migrated — nothing to do.")
        return

    if not (_table_exists(cr, ZONE_REL) and _table_exists(cr, SUBZONE_REL)):
        # Fresh install or already cleaned: attach any stray zones to the
        # oldest company so the NOT NULL constraint can be applied.
        cr.execute("SELECT id FROM accurate_delivery_company ORDER BY id LIMIT 1")
        row = cr.fetchone()
        if row:
            cr.execute(
                "UPDATE accurate_zone SET company_id = %s WHERE company_id IS NULL",
                (row[0],),
            )
        return

    # ── 1. every zone → the lowest-id company it was linked to ───────────────
    cr.execute("""
        WITH links AS (
            SELECT zone_id, company_id FROM %s
            UNION
            SELECT zone_id, company_id FROM %s
        )
        UPDATE accurate_zone z
           SET company_id = l.owner
          FROM (SELECT zone_id, MIN(company_id) AS owner
                  FROM links GROUP BY zone_id) l
         WHERE z.id = l.zone_id AND z.company_id IS NULL
    """ % (ZONE_REL, SUBZONE_REL))
    _logger.info("Assigned an owner to %s zones", cr.rowcount)

    # Zones that were linked to nothing (shouldn't happen) → oldest company.
    cr.execute("SELECT id FROM accurate_delivery_company ORDER BY id LIMIT 1")
    fallback = cr.fetchone()
    if fallback:
        cr.execute(
            "UPDATE accurate_zone SET company_id = %s WHERE company_id IS NULL",
            (fallback[0],),
        )
        if cr.rowcount:
            _logger.warning("%s orphan zones assigned to company %s",
                            cr.rowcount, fallback[0])

    # ── 2. copies for every OTHER company that shared a zone ─────────────────
    cr.execute("""
        WITH links AS (
            SELECT zone_id, company_id FROM %s
            UNION
            SELECT zone_id, company_id FROM %s
        )
        SELECT DISTINCT l.company_id
          FROM links l
          JOIN accurate_zone z ON z.id = l.zone_id
         WHERE l.company_id != z.company_id
         ORDER BY l.company_id
    """ % (ZONE_REL, SUBZONE_REL))
    other_companies = [r[0] for r in cr.fetchall()]

    for company_id in other_companies:
        # 2a. parent zones first — keep old→new so children can be re-parented.
        cr.execute("""
            WITH links AS (
                SELECT zone_id, company_id FROM %s
                UNION
                SELECT zone_id, company_id FROM %s
            )
            INSERT INTO accurate_zone
                (company_id, api_id, name, is_subzone, in_price_list,
                 price_list_validated_at, parent_id, create_uid, create_date,
                 write_uid, write_date)
            SELECT %%s, z.api_id, z.name, z.is_subzone, z.in_price_list,
                   z.price_list_validated_at, NULL, z.create_uid, z.create_date,
                   z.write_uid, now() AT TIME ZONE 'UTC'
              FROM accurate_zone z
              JOIN links l ON l.zone_id = z.id AND l.company_id = %%s
             WHERE z.company_id != %%s AND NOT z.is_subzone
            RETURNING id, api_id
        """ % (ZONE_REL, SUBZONE_REL), (company_id, company_id, company_id))
        new_parents = {api_id: new_id for new_id, api_id in cr.fetchall()}
        _logger.info("Company %s: copied %s parent zones", company_id, len(new_parents))

        # 2b. sub-zones, re-parented onto this company's own parent copies.
        cr.execute("""
            WITH links AS (
                SELECT zone_id, company_id FROM %s
                UNION
                SELECT zone_id, company_id FROM %s
            )
            SELECT z.id, z.api_id, z.name, z.in_price_list,
                   z.price_list_validated_at, p.api_id AS parent_api_id,
                   z.create_uid, z.create_date, z.write_uid
              FROM accurate_zone z
              JOIN links l ON l.zone_id = z.id AND l.company_id = %%s
         LEFT JOIN accurate_zone p ON p.id = z.parent_id
             WHERE z.company_id != %%s AND z.is_subzone
        """ % (ZONE_REL, SUBZONE_REL), (company_id, company_id))
        subzones = cr.fetchall()

        copied_subs = 0
        for (_old_id, api_id, name, in_price_list, validated_at,
             parent_api_id, create_uid, create_date, write_uid) in subzones:
            new_parent = new_parents.get(parent_api_id)
            if not new_parent:
                # Parent wasn't shared with this company — keep the sub-zone
                # with its original owner rather than orphaning it.
                _logger.warning(
                    "Company %s: sub-zone api_id %s has no parent copy "
                    "(parent api_id %s) — left with its original owner",
                    company_id, api_id, parent_api_id,
                )
                continue
            cr.execute("""
                INSERT INTO accurate_zone
                    (company_id, api_id, name, is_subzone, in_price_list,
                     price_list_validated_at, parent_id, create_uid,
                     create_date, write_uid, write_date)
                VALUES (%s, %s, %s, TRUE, %s, %s, %s, %s, %s, %s,
                        now() AT TIME ZONE 'UTC')
            """, (company_id, api_id, name, in_price_list, validated_at,
                  new_parent, create_uid, create_date, write_uid))
            copied_subs += 1
        _logger.info("Company %s: copied %s sub-zones", company_id, copied_subs)

        # ── 3. re-point this company's live records to its own copies ────────
        # Synced zones are matched on api_id; manually-created ones (api_id 0)
        # have no id to match, so they fall back to an exact name match.
        for table, columns, company_col in LIVE_REFS:
            if not _table_exists(cr, table):
                continue
            for column in columns:
                if not _column_exists(cr, table, column):
                    continue
                cr.execute("""
                    UPDATE {table} t
                       SET {column} = mine.id
                      FROM accurate_zone old, accurate_zone mine
                     WHERE t.{column} = old.id
                       AND t.{company_col} = %s
                       AND old.company_id != %s
                       AND mine.company_id = %s
                       AND mine.is_subzone = old.is_subzone
                       AND ((old.api_id != 0 AND mine.api_id = old.api_id)
                            OR (old.api_id = 0 AND mine.api_id = 0
                                AND mine.name = old.name))
                """.format(table=table, column=column, company_col=company_col),
                    (company_id, company_id, company_id))
                if cr.rowcount:
                    _logger.info("Company %s: re-pointed %s.%s on %s rows",
                                 company_id, table, column, cr.rowcount)

    # ── 4. the link tables are obsolete ──────────────────────────────────────
    cr.execute("SELECT count(*) FROM accurate_zone WHERE company_id IS NULL")
    remaining = cr.fetchone()[0]
    if remaining:
        raise Exception(
            "Zone migration aborted: %s zones still have no delivery company."
            % remaining
        )
    cr.execute("DROP TABLE IF EXISTS %s" % ZONE_REL)
    cr.execute("DROP TABLE IF EXISTS %s" % SUBZONE_REL)
    _logger.info("Per-company zone migration complete; link tables dropped.")
