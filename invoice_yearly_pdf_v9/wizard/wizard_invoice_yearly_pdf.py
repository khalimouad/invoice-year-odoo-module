# -*- coding: utf-8 -*-
import base64
from datetime import date

from openerp import api, fields, models, _
from openerp.exceptions import UserError

POSTED_STATES = ('open', 'paid')


class WizardInvoiceYearlyPdf(models.TransientModel):
    _name = 'wizard.invoice.yearly.pdf'
    _description = 'Generate Yearly Invoice PDF'

    year = fields.Integer(
        string='Year',
        required=True,
        default=lambda self: date.today().year - 1,
    )
    invoice_type = fields.Selection([
        ('out_invoice', 'Customer Invoices'),
        ('out_refund', 'Customer Credit Notes'),
        ('both', 'Both'),
    ], string='Invoice Type', required=True, default='out_invoice')
    include_paid = fields.Boolean(
        string='Include Paid Invoices',
        default=True,
    )

    # Dynamically populated with all QWeb reports whose model is account.invoice
    report_id = fields.Many2one(
        'ir.actions.report.xml',   # Odoo 9 model name
        string='Print Template',
        required=True,
        domain="[('model', '=', 'account.invoice'), ('report_type', 'like', 'qweb')]",
        help='Choose a template from the list — same templates available in the invoice Print menu.',
    )

    @api.model
    def default_get(self, fields_list):
        res = super(WizardInvoiceYearlyPdf, self).default_get(fields_list)
        # In Odoo 9 the canonical invoice report XML id is `account.account_invoices`
        # (its `report_name` is `account.report_invoice`).
        default_report = self.env.ref('account.account_invoices', raise_if_not_found=False)
        if not default_report:
            default_report = self.env['ir.actions.report.xml'].search([
                ('model', '=', 'account.invoice'),
                ('report_type', 'like', 'qweb'),
            ], limit=1)
        if default_report:
            res['report_id'] = default_report.id
        return res

    @api.constrains('year')
    def _check_year(self):
        for rec in self:
            if rec.year < 2000 or rec.year > date.today().year + 1:
                raise UserError(_('Please enter a valid year (2000 – %d).') % (date.today().year + 1))

    def action_generate_pdf(self):
        self.ensure_one()
        invoices = self._fetch_invoices()
        if not invoices:
            raise UserError(_(
                'No posted invoices found for %d with the selected type.'
            ) % self.year)

        generator = self.env['invoice.yearly.pdf']
        pdf_parts = []
        for inv in invoices:
            try:
                pdf_parts.append(self._render_pdf(inv))
            except Exception:
                pass

        if not pdf_parts:
            raise UserError(_('None of the invoices could be rendered as PDF.'))

        merged = generator._merge_pdfs(pdf_parts)
        filename = 'Invoices_%d.pdf' % self.year

        record_name = 'Invoices %d (Manual)' % self.year
        record = self.env['invoice.yearly.pdf'].search(
            [('name', '=', record_name)], limit=1
        )
        if not record:
            record = self.env['invoice.yearly.pdf'].create({
                'name': record_name,
                'year': self.year,
            })

        if record.attachment_id:
            record.attachment_id.unlink()

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(merged),
            'datas_fname': filename,
            'res_model': 'invoice.yearly.pdf',
            'res_id': record.id,
            'mimetype': 'application/pdf',
        })

        record.write({
            'state': 'done',
            'attachment_id': attachment.id,
            'invoice_count': len(pdf_parts),
            'generated_on': fields.Datetime.now(),
        })

        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % attachment.id,
            'target': 'new',
        }

    def _fetch_invoices(self):
        date_from = '%d-01-01' % self.year
        date_to = '%d-12-31' % self.year
        types = ('out_invoice', 'out_refund') if self.invoice_type == 'both' else (self.invoice_type,)
        states = list(POSTED_STATES) if self.include_paid else ['open']
        return self.env['account.invoice'].search([
            ('type', 'in', types),
            ('state', 'in', states),
            ('date_invoice', '>=', date_from),
            ('date_invoice', '<=', date_to),
        ], order='date_invoice asc, number asc')

    def _render_pdf(self, invoice):
        # Odoo 9: env['report'].get_pdf returns PDF bytes directly (NOT a tuple)
        return self.env['report'].get_pdf(invoice, self.report_id.report_name)
