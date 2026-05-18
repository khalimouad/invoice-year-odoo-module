# -*- coding: utf-8 -*-
{
    'name': 'Invoice Yearly PDF Export (Odoo 13)',
    'version': '13.0.1.0.0',
    'summary': 'Fix CSS in background PDFs (local base_url) + auto-update cron from Git',
    'author': 'Custom',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron.xml',
        'views/wizard_views.xml',
        'views/credit_note_wizard_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': False,
}
