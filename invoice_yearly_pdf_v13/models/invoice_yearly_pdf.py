# -*- coding: utf-8 -*-
import base64
import io
import logging
from datetime import date

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Commit progress to DB every N invoices so other sessions can read it.
_PROGRESS_BATCH = 10


class InvoiceYearlyPdf(models.Model):
    _name = 'invoice.yearly.pdf'
    _description = 'Yearly Invoice PDF Generator'

    name = fields.Char(string='Name', required=True)
    year = fields.Integer(string='Year', required=True)
    report_id = fields.Many2one(
        'ir.actions.report', string='Report Template', ondelete='set null')
    invoice_type_filter = fields.Selection([
        ('out_invoice', 'Customer Invoices'),
        ('out_refund', 'Customer Credit Notes'),
        ('both', 'Both'),
    ], string='Invoice Type', default='both')
    attachment_id = fields.Many2one('ir.attachment', string='Generated PDF', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('error', 'Error'),
    ], default='draft', string='State', readonly=True)

    # Progress tracking
    total_count = fields.Integer(string='Total Documents', readonly=True)
    processed_count = fields.Integer(string='Processed', readonly=True)
    progress_pct = fields.Integer(
        string='Progress', compute='_compute_progress_pct', store=False)

    invoice_count = fields.Integer(string='Documents Merged', readonly=True)
    error_message = fields.Text(string='Error / Notes', readonly=True)
    generated_on = fields.Datetime(string='Generated On', readonly=True)

    @api.depends('processed_count', 'total_count')
    def _compute_progress_pct(self):
        for rec in self:
            if rec.state == 'done':
                rec.progress_pct = 100
            elif rec.total_count:
                rec.progress_pct = int(rec.processed_count * 100 / rec.total_count)
            else:
                rec.progress_pct = 0

    @api.model
    def _get_posted_invoices(self, year):
        date_from = date(year, 1, 1)
        date_to = date(year, 12, 31)
        inv_types = ('out_invoice', 'out_refund')
        if self.invoice_type_filter and self.invoice_type_filter != 'both':
            inv_types = (self.invoice_type_filter,)
        # Odoo 13: field is `type` (renamed to `move_type` in v14)
        return self.env['account.move'].search([
            ('type', 'in', inv_types),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
        ], order='invoice_date asc, name asc')

    def _render_invoice_pdf(self, invoice):
        report = self.report_id or self.env.ref('account.account_invoices')
        # Odoo 13: render_qweb_pdf (renamed _render_qweb_pdf in v16)
        pdf, _ct = report.render_qweb_pdf(invoice.ids)
        return pdf

    @api.model
    def _merge_pdfs(self, pdf_list):
        try:
            from pypdf import PdfWriter as _Merger        # pypdf (modern)
        except ImportError:
            try:
                from PyPDF2 import PdfMerger as _Merger  # PyPDF2 >= 2.0
            except ImportError:
                from PyPDF2 import PdfFileMerger as _Merger  # PyPDF2 1.x

        writer = _Merger()
        for pdf_bytes in pdf_list:
            # import_bookmarks=False avoids PdfReadError on wkhtmltopdf PDFs
            # which use non-standard anchors (/__WKANCHOR_x).
            try:
                writer.append(io.BytesIO(pdf_bytes), import_bookmarks=False)
            except TypeError:
                writer.append(io.BytesIO(pdf_bytes))
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    def _commit(self, vals):
        """Write vals and commit so the progress is visible to other sessions."""
        self.write(vals)
        self.env.cr.commit()

    def action_generate(self, year=None):
        self.ensure_one()
        target_year = year or self.year
        try:
            invoices = self._get_posted_invoices(target_year)
            if not invoices:
                self._commit({
                    'state': 'error',
                    'error_message': 'No posted invoices found for year %d.' % target_year,
                })
                return False

            total = len(invoices)
            # Publish total immediately so the progress bar starts from 0/N
            self._commit({
                'state': 'running',
                'total_count': total,
                'processed_count': 0,
            })

            pdf_parts = []
            failed = 0
            for i, inv in enumerate(invoices, 1):
                try:
                    pdf_parts.append(self._render_invoice_pdf(inv))
                except Exception as e:
                    _logger.warning('Could not render invoice %s: %s', inv.name, e)
                    failed += 1

                # Commit progress every _PROGRESS_BATCH documents
                if i % _PROGRESS_BATCH == 0 or i == total:
                    self._commit({'processed_count': i})

            if not pdf_parts:
                raise ValueError('All %d invoices failed to render.' % total)

            merged_pdf = self._merge_pdfs(pdf_parts)
            doc_type = 'CreditNotes' if self.invoice_type_filter == 'out_refund' else 'Invoices'
            filename = '%s_%d.pdf' % (doc_type, target_year)

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

            msg = '%d document(s) merged.' % len(pdf_parts)
            if failed:
                msg += ' %d skipped due to render errors.' % failed

            self._commit({
                'state': 'done',
                'attachment_id': attachment.id,
                'invoice_count': len(pdf_parts),
                'processed_count': total,
                'generated_on': fields.Datetime.now(),
                'error_message': msg if failed else False,
            })
            _logger.info('invoice.yearly.pdf [%s]: %s', self.name, msg)
            return True

        except Exception as e:
            _logger.error('invoice.yearly.pdf [%s] failed: %s', self.name, e)
            # Commit the error state so it survives any outer rollback
            self._commit({'state': 'error', 'error_message': str(e)})
            return False

    def action_generate_background(self):
        """Schedule a one-shot ir.cron to run generation outside the HTTP request."""
        self.ensure_one()
        self._commit({'state': 'running', 'total_count': 0, 'processed_count': 0})
        ir_model = self.env['ir.model'].search([('model', '=', self._name)], limit=1)
        self.env['ir.cron'].sudo().create({
            'name': 'Generate Yearly PDF: %s' % self.name,
            'model_id': ir_model.id,
            'state': 'code',
            'code': 'model.browse([%d]).action_generate()' % self.id,
            'interval_number': 1,
            'interval_type': 'minutes',
            'numbercall': 1,
            'nextcall': fields.Datetime.now(),
            'doall': False,
            'active': True,
            'priority': 5,
        })

    @api.model
    def cron_generate_yearly_pdf(self):
        previous_year = date.today().year - 1
        record_name = 'Invoices %d (Auto)' % previous_year
        record = self.search([('name', '=', record_name)], limit=1)
        if not record:
            record = self.create({
                'name': record_name,
                'year': previous_year,
                'invoice_type_filter': 'both',
            })
        record.action_generate(year=previous_year)
