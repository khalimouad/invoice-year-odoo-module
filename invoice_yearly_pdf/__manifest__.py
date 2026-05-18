# -*- coding: utf-8 -*-
{
    'name': 'Invoice Yearly PDF Export',
    'version': '1.0.0',
    'summary': 'Generate a merged PDF of all posted invoices for a chosen year',
    'description': """
        Generates a single merged PDF containing all posted invoices for a
        selected fiscal year (January 1st – December 31st).

        Features:
        - Wizard to pick any year manually
        - Automated cron that runs yearly (configurable)
        - Uses the standard Odoo QWeb invoice report template
        - Attaches the merged PDF to an ir.attachment record
        - Compatible with Odoo 9 and Odoo 13
    """,
    'author': 'Custom',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron.xml',
        'views/wizard_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': False,
}
