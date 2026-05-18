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
    report_ref = fields.Char(
        string='QWeb Report Reference',
        required=True,
        default='account.report_invoice',
        help='External ID of the QWeb report to use for rendering.\n'
             'Odoo 13: account.report_invoice\n'
             'Odoo 9:  account.report_invoice_with_payments',
    )

    @api.constrains('year')
    def _check_year(self):
        for rec in self:
            if rec.year < 2000 or rec.year > date.today().year + 1:
                raise UserError(_('Please enter a valid year (2000 – %d).') % (date.today().year + 1))

    def action_generate_pdf(self):
        self.ensure_one()
        generator = self.env['invoice.yearly.pdf']
        invoices = self._fetch_invoices()

        if not invoices:
            raise UserError(_(
                'No posted invoices found for the year %d with the selected type.'
            ) % self.year)

        # Render + merge
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
        encoded = base64.b64encode(merged)

        # Create a persistent record so the attachment is reachable later
        record_name = 'Invoices %d (Manual)' % self.year
        existing = self.env['invoice.yearly.pdf'].search(
            [('name', '=', record_name)], limit=1
        )
        if existing:
            record = existing
        else:
            record = self.env['invoice.yearly.pdf'].create({
                'name': record_name,
                'year': self.year,
            })

        if record.attachment_id:
            record.attachment_id.unlink()

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': encoded,
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

        # Return an action to download the PDF immediately
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % attachment.id,
            'target': 'new',
        }

    def _fetch_invoices(self):
        date_from = date(self.year, 1, 1)
        date_to = date(self.year, 12, 31)

        if self.invoice_type == 'both':
            types = ('out_invoice', 'out_refund')
        else:
            types = (self.invoice_type,)

        # Odoo 13
        if hasattr(self.env['account.move'], 'move_type'):
            return self.env['account.move'].search([
                ('move_type', 'in', types),
                ('state', '=', 'posted'),
                ('invoice_date', '>=', date_from),
                ('invoice_date', '<=', date_to),
            ], order='invoice_date asc, name asc')

        # Odoo 9
        state_filter = 'open'  # posted invoices in Odoo 9
        return self.env['account.invoice'].search([
            ('type', 'in', types),
            ('state', '=', state_filter),
            ('date_invoice', '>=', fields.Date.to_string(date_from)),
            ('date_invoice', '<=', fields.Date.to_string(date_to)),
        ], order='date_invoice asc, number asc')

    def _render_pdf(self, invoice):
        report_ref = self.report_ref or 'account.report_invoice'
        try:
            report = self.env.ref(report_ref)
        except ValueError:
            report = self.env.ref('account.report_invoice_with_payments')

        if hasattr(report, '_render_qweb_pdf'):
            pdf, _ = report._render_qweb_pdf(invoice.ids)
            return pdf

        pdf, _ = self.env['report'].get_pdf(invoice, report_ref)
        return pdf
