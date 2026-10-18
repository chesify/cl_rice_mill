import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Storage is charged from the company setting only (G20): the "Storage" service rates are archived
    before the service type disappears."""
    cr.execute("""
        UPDATE cl_rice_service_rate
           SET active = FALSE, service_type = 'other'
         WHERE service_type = 'storage'
     RETURNING id
    """)
    archived = cr.fetchall()
    if archived:
        _logger.info("cl_rice_mill: %s storage service rates archived (the storage settings are used)", len(archived))
