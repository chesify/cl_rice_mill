def migrate(cr, version):
    """Interim payouts never carry a balance forward: the hidden 'carry forward' choice blocked saving them."""
    cr.execute("""
        UPDATE cl_rice_settlement
           SET negative_policy = 'keep', next_season_id = NULL
         WHERE settlement_type = 'interim'
           AND state = 'draft'
           AND (negative_policy IS DISTINCT FROM 'keep' OR next_season_id IS NOT NULL)
    """)
