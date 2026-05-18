# -*- coding: utf-8 -*-
import base64
import io
import logging
from datetime import date

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# QWeb report external ID differs between Odoo 9 and 13.
# Odoo 13 uses account.report_invoice; Odoo 9 uses account.report_invoice_with_payments
# or account.report_invoice depending on the version. We try the standard one first.
INVOICE_REPORT_REF = 'account.report_invoice'


class InvoiceYearlyPdf(models.Model):
    _name = 'invoice.yearly.pdf'
    _description = 'Yearly Invoice PDF Generator'

    name = fields.Char(string='Name', required=True)
    year = fields.Integer(string='Year', required=True)
    attachment_id = fields.Many2one('ir.attachment', string='Generated PDF', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='draft', string='State', readonly=True)
    invoice_count = fields.Integer(string='Invoices Included', readonly=True)
    error_message = fields.Text(string='Error', readonly=True)
    generated_on = fields.Datetime(string='Generated On', readonly=True)

    @api.model
    def _get_posted_invoices(self, year):
        """Return all posted customer invoices for the given year."""
        date_from = date(year, 1, 1)
        date_to = date(year, 12, 31)

        # Odoo 13: account.move with move_type in ('out_invoice', 'out_refund')
        # Odoo 9:  account.invoice with type in ('out_invoice', 'out_refund')
        if hasattr(self.env['account.move'], 'move_type'):
            # Odoo 13+
            invoices = self.env['account.move'].search([
                ('move_type', 'in', ('out_invoice', 'out_refund')),
                ('state', '=', 'posted'),
                ('invoice_date', '>=', date_from),
                ('invoice_date', '<=', date_to),
            ], order='invoice_date asc, name asc')
        else:
            # Odoo 9 / legacy
            invoices = self.env['account.invoice'].search([
                ('type', 'in', ('out_invoice', 'out_refund')),
                ('state', '=', 'open'),          # 'open' == posted in Odoo 9
                ('date_invoice', '>=', fields.Date.to_string(date_from)),
                ('date_invoice', '<=', fields.Date.to_string(date_to)),
            ], order='date_invoice asc, number asc')
        return invoices

    @api.model
    def _merge_pdfs(self, pdf_list):
        """Merge a list of PDF byte-strings into one PDF using PyPDF2 or pypdf."""
        try:
            # pypdf (PyPDF2 successor, available on newer systems)
            from pypdf import PdfWriter
        except ImportError:
            try:
                from PyPDF2 import PdfMerger as PdfWriter
                writer = PdfWriter()
                for pdf_bytes in pdf_list:
                    writer.append(io.BytesIO(pdf_bytes))
                output = io.BytesIO()
                writer.write(output)
                return output.getvalue()
            except ImportError:
                raise ImportError(
                    "PyPDF2 or pypdf is required to merge PDFs. "
                    "Install it with: pip install pypdf"
                )

        writer = PdfWriter()
        for pdf_bytes in pdf_list:
            writer.append(io.BytesIO(pdf_bytes))
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    @api.model
    def _render_invoice_pdf(self, invoices):
        """Render QWeb PDF for a recordset of invoices."""
        report_ref = INVOICE_REPORT_REF
        # Try to resolve the report; fall back to with_payments variant
        try:
            report = self.env.ref(report_ref)
        except ValueError:
            report_ref = 'account.report_invoice_with_payments'
            report = self.env.ref(report_ref)

        # Odoo 13 render API
        if hasattr(report, '_render_qweb_pdf'):
            pdf_content, _ = report._render_qweb_pdf(invoices.ids)
            return pdf_content

        # Odoo 9 / older render API
        pdf_content, _ = self.env['report'].get_pdf(invoices, report_ref)
        return pdf_content

    def action_generate(self, year=None):
        """Generate the merged yearly PDF and store it as an attachment."""
        self.ensure_one()
        target_year = year or self.year
        try:
            invoices = self._get_posted_invoices(target_year)
            if not invoices:
                self.write({
                    'state': 'error',
                    'error_message': 'No posted invoices found for year %d.' % target_year,
                })
                return False

            # Render each invoice individually then merge, to avoid memory
            # issues on very large datasets and to tolerate per-invoice failures.
            pdf_parts = []
            failed = 0
            for inv in invoices:
                try:
                    pdf_parts.append(self._render_invoice_pdf(inv))
                except Exception as e:
                    _logger.warning('Could not render invoice %s: %s', inv.name, e)
                    failed += 1

            if not pdf_parts:
                raise ValueError('All %d invoices failed to render.' % len(invoices))

            merged_pdf = self._merge_pdfs(pdf_parts)
            filename = 'Invoices_%d.pdf' % target_year

            # Remove old attachment if any
            if self.attachment_id:
                self.attachment_id.unlink()

            attachment = self.env['ir.attachment'].create({
                'name': filename,
                'type': 'binary',
                'datas': base64.b64encode(merged_pdf),
                'res_model': self._name,
                'res_id': self.id,
                'mimetype': 'application/pdf',
            })

            msg = '%d invoice(s) merged into PDF.' % len(pdf_parts)
            if failed:
                msg += ' %d invoice(s) skipped due to render errors.' % failed

            self.write({
                'state': 'done',
                'attachment_id': attachment.id,
                'invoice_count': len(pdf_parts),
                'generated_on': fields.Datetime.now(),
                'error_message': (msg if failed else False),
            })
            _logger.info('invoice.yearly.pdf [%s]: %s', self.name, msg)
            return True

        except Exception as e:
            _logger.error('invoice.yearly.pdf [%s] failed: %s', self.name, e)
            self.write({'state': 'error', 'error_message': str(e)})
            return False

    @api.model
    def cron_generate_yearly_pdf(self):
        """Called by the scheduled action. Generates PDF for the previous year."""
        previous_year = date.today().year - 1
        record_name = 'Invoices %d (Auto)' % previous_year

        existing = self.search([('name', '=', record_name)], limit=1)
        if existing:
            record = existing
        else:
            record = self.create({'name': record_name, 'year': previous_year})

        record.action_generate(year=previous_year)
