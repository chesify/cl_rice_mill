def migrate(cr, version):
    """Intakes get a required partition: take the location their receipt was put in."""
    cr.execute("ALTER TABLE cl_rice_intake ADD COLUMN IF NOT EXISTS location_id integer")
    cr.execute("""
        UPDATE cl_rice_intake intake
           SET location_id = picking.location_dest_id
          FROM stock_picking picking
         WHERE picking.id = intake.picking_id
           AND intake.location_id IS NULL
    """)
    cr.execute("""
        UPDATE cl_rice_intake intake
           SET location_id = warehouse.lot_stock_id
          FROM stock_warehouse warehouse
         WHERE warehouse.id = intake.warehouse_id
           AND intake.location_id IS NULL
    """)
