# -*- coding: utf-8 -*-
import base64
import io
import logging
from datetime import date

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


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
        date_from = date(year, 1, 1)
        date_to = date(year, 12, 31)
        # Odoo 13: account.move field is `type` (renamed to `move_type` in v14)
        return self.env['account.move'].search([
            ('type', 'in', ('out_invoice', 'out_refund')),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
        ], order='invoice_date asc, name asc')

    @api.model
    def _render_invoice_pdf(self, invoice):
        # Odoo 13: public method is `render_qweb_pdf` (got `_` prefix in v16)
        report = self.env.ref('account.account_invoices')
        pdf, _ = report.render_qweb_pdf(invoice.ids)
        return pdf

    @api.model
    def _merge_pdfs(self, pdf_list):
        try:
            from pypdf import PdfWriter
        except ImportError:
            from PyPDF2 import PdfMerger as PdfWriter
            writer = PdfWriter()
            for pdf_bytes in pdf_list:
                writer.append(io.BytesIO(pdf_bytes))
            out = io.BytesIO()
            writer.write(out)
            return out.getvalue()

        writer = PdfWriter()
        for pdf_bytes in pdf_list:
            writer.append(io.BytesIO(pdf_bytes))
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    def action_generate(self, year=None):
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

            msg = '%d invoice(s) merged.' % len(pdf_parts)
            if failed:
                msg += ' %d skipped due to render errors.' % failed

            self.write({
                'state': 'done',
                'attachment_id': attachment.id,
                'invoice_count': len(pdf_parts),
                'generated_on': fields.Datetime.now(),
                'error_message': msg if failed else False,
            })
            _logger.info('invoice.yearly.pdf [%s]: %s', self.name, msg)
            return True

        except Exception as e:
            _logger.error('invoice.yearly.pdf [%s] failed: %s', self.name, e)
            self.write({'state': 'error', 'error_message': str(e)})
            return False

    @api.model
    def cron_generate_yearly_pdf(self):
        previous_year = date.today().year - 1
        record_name = 'Invoices %d (Auto)' % previous_year
        record = self.search([('name', '=', record_name)], limit=1)
        if not record:
            record = self.create({'name': record_name, 'year': previous_year})
        record.action_generate(year=previous_year)
