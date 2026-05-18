# -*- coding: utf-8 -*-
{
    'name': 'Invoice Yearly PDF Export (Odoo 13)',
    'version': '13.0.1.0.0',
    'summary': 'Generate a merged PDF of all posted invoices for a chosen year — Odoo 13',
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
