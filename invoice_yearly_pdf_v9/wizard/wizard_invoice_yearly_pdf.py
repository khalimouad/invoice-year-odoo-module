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
    include_paid = fields.Boolean(string='Include Paid Invoices', default=True)
    report_id = fields.Many2one(
        'ir.actions.report.xml',
        string='Print Template',
        required=True,
        domain="[('model', '=', 'account.invoice'), ('report_type', 'like', 'qweb')]",
    )

    @api.model
    def default_get(self, fields_list):
        res = super(WizardInvoiceYearlyPdf, self).default_get(fields_list)
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

    def _get_or_create_record(self, name):
        record = self.env['invoice.yearly.pdf'].search([('name', '=', name)], limit=1)
        if not record:
            record = self.env['invoice.yearly.pdf'].create({
                'name': name,
                'year': self.year,
            })
        return record

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

    # ── Synchronous (small datasets) ────────────────────────────────────────

    def action_generate_pdf(self):
        self.ensure_one()
        invoices = self._fetch_invoices()
        if not invoices:
            raise UserError(_('No posted invoices found for %d with the selected type.') % self.year)

        generator = self.env['invoice.yearly.pdf']
        pdf_parts = []
        for inv in invoices:
            try:
                pdf_parts.append(self.env['report'].get_pdf(inv, self.report_id.report_name))
            except Exception:
                pass

        if not pdf_parts:
            raise UserError(_('None of the invoices could be rendered as PDF.'))

        merged = generator._merge_pdfs(pdf_parts)
        filename = 'Invoices_%d.pdf' % self.year

        record = self._get_or_create_record('Invoices %d (Manual)' % self.year)
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
            'report_id': self.report_id.id,
            'invoice_type_filter': self.invoice_type,
            'attachment_id': attachment.id,
            'invoice_count': len(pdf_parts),
            'generated_on': fields.Datetime.now(),
        })

        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % attachment.id,
            'target': 'new',
        }

    # ── Background (large datasets) ─────────────────────────────────────────

    def action_generate_pdf_background(self):
        self.ensure_one()
        record = self._get_or_create_record('Invoices %d (Manual)' % self.year)
        record.write({
            'report_id': self.report_id.id,
            'invoice_type_filter': self.invoice_type,
        })
        record.action_generate_background()
        # Odoo 9 has no display_notification — open the history list directly
        return {
            'type': 'ir.actions.act_window',
            'name': 'Generated PDFs History',
            'res_model': 'invoice.yearly.pdf',
            'view_mode': 'tree,form',
            'target': 'current',
        }
