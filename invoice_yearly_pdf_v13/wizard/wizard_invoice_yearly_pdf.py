# -*- coding: utf-8 -*-
import base64
from datetime import date

from odoo import api, fields, models, _
from odoo.exceptions import UserError


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

    # Dynamically populated with all QWeb reports whose model is account.move
    report_id = fields.Many2one(
        'ir.actions.report',
        string='Print Template',
        required=True,
        domain="[('model', '=', 'account.move'), ('report_type', 'like', 'qweb')]",
        help='Choose a template from the list — same templates available in the invoice Print menu.',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Pre-select the standard invoice report if it exists
        default_report = self.env['ir.actions.report'].search([
            ('report_name', '=', 'account.report_invoice'),
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
        date_from = date(self.year, 1, 1)
        date_to = date(self.year, 12, 31)
        types = ('out_invoice', 'out_refund') if self.invoice_type == 'both' else (self.invoice_type,)
        return self.env['account.move'].search([
            ('move_type', 'in', types),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
        ], order='invoice_date asc, name asc')

    def _render_pdf(self, invoice):
        pdf, _ = self.report_id._render_qweb_pdf(invoice.ids)
        return pdf
