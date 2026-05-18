# -*- coding: utf-8 -*-
import base64
import io
import logging
from datetime import date, timedelta

from openerp import api, fields, models

_logger = logging.getLogger(__name__)

POSTED_STATES = ('open', 'paid')
_PROGRESS_BATCH = 10   # commit UI counter every N invoices
_SAVE_BATCH = 50       # merge + save partial PDF every N invoices
_STUCK_HOURS = 2


class InvoiceYearlyPdf(models.Model):
    _name = 'invoice.yearly.pdf'
    _description = 'Yearly Invoice PDF Generator'

    name = fields.Char(string='Name', required=True)
    year = fields.Integer(string='Year', required=True)
    report_id = fields.Many2one(
        'ir.actions.report.xml', string='Report Template', ondelete='set null')
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
    started_on = fields.Datetime(string='Started On', readonly=True)
    total_count = fields.Integer(string='Total Documents', readonly=True)
    processed_count = fields.Integer(string='Processed', readonly=True)
    # Last invoice index saved into the partial PDF (resume point)
    partial_count = fields.Integer(string='Partial Saved At', readonly=True)
    progress_pct = fields.Integer(
        string='Progress', compute='_compute_progress_pct', store=False)
    is_stuck = fields.Boolean(
        string='Stuck?', compute='_compute_progress_pct', store=False)

    invoice_count = fields.Integer(string='Documents Merged', readonly=True)
    error_message = fields.Text(string='Error / Notes', readonly=True)
    generated_on = fields.Datetime(string='Generated On', readonly=True)

    @api.depends('processed_count', 'total_count', 'state', 'started_on')
    def _compute_progress_pct(self):
        stuck_threshold = fields.Datetime.now() - timedelta(hours=_STUCK_HOURS)
        for rec in self:
            if rec.state == 'done':
                rec.progress_pct = 100
            elif rec.total_count:
                rec.progress_pct = int(rec.processed_count * 100 / rec.total_count)
            else:
                rec.progress_pct = 0
            rec.is_stuck = (
                rec.state == 'running'
                and rec.started_on
                and rec.started_on < stuck_threshold
            )

    # ── Partial attachment helpers ──────────────────────────────────────────

    def _get_partial_attachment(self):
        return self.env['ir.attachment'].search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('name', '=', '_partial.pdf'),
        ], limit=1)

    def _load_partial_bytes(self):
        att = self._get_partial_attachment()
        return base64.b64decode(att.datas) if att else None

    def _save_partial(self, pdf_bytes):
        att = self._get_partial_attachment()
        encoded = base64.b64encode(pdf_bytes)
        if att:
            att.write({'datas': encoded})
        else:
            self.env['ir.attachment'].create({
                'name': '_partial.pdf',
                'type': 'binary',
                'datas': encoded,
                'datas_fname': '_partial.pdf',
                'res_model': self._name,
                'res_id': self.id,
                'mimetype': 'application/pdf',
            })

    def _delete_partial(self):
        self._get_partial_attachment().unlink()

    # ── Core helpers ────────────────────────────────────────────────────────

    def _commit(self, vals):
        self.write(vals)
        self.env.cr.commit()

    @api.model
    def _get_posted_invoices(self, year):
        date_from = '%d-01-01' % year
        date_to = '%d-12-31' % year
        inv_types = ('out_invoice', 'out_refund')
        if self.invoice_type_filter and self.invoice_type_filter != 'both':
            inv_types = (self.invoice_type_filter,)
        return self.env['account.invoice'].search([
            ('type', 'in', inv_types),
            ('state', 'in', POSTED_STATES),
            ('date_invoice', '>=', date_from),
            ('date_invoice', '<=', date_to),
        ], order='date_invoice asc, number asc')

    def _render_invoice_pdf(self, invoice):
        report_name = self.report_id.report_name if self.report_id else 'account.report_invoice'
        # In cron context there is no HTTP request, so wkhtmltopdf cannot resolve
        # relative URLs for CSS/fonts. Passing base_url explicitly fixes the issue.
        ICP = self.env['ir.config_parameter'].sudo()
        base_url = ICP.get_param('report.url') or ICP.get_param('web.base.url')
        return self.env['report'].with_context(base_url=base_url).get_pdf(invoice, report_name)

    @api.model
    def _merge_pdfs(self, pdf_list):
        try:
            from PyPDF2 import PdfFileMerger as _Merger  # PyPDF2 1.x
        except ImportError:
            try:
                from PyPDF2 import PdfMerger as _Merger  # PyPDF2 >= 2.0
            except ImportError:
                from pypdf import PdfWriter as _Merger   # pypdf (modern)

        writer = _Merger()
        for pdf_bytes in pdf_list:
            try:
                writer.append(io.BytesIO(pdf_bytes), import_bookmarks=False)
            except TypeError:
                writer.append(io.BytesIO(pdf_bytes))
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    # ── Main generation (resumable) ─────────────────────────────────────────

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

            # Resume: skip invoices already saved into the partial PDF.
            # partial_count is the authoritative resume point — it reflects
            # how many invoices were actually written to the partial, which
            # may be less than processed_count (last progress commit).
            skip = 0
            if self.partial_count > 0 and self._get_partial_attachment():
                skip = self.partial_count
                _logger.info('[%s] Resuming from %d / %d', self.name, skip, total)

            self._commit({
                'state': 'running',
                'started_on': fields.Datetime.now(),
                'total_count': total,
                'processed_count': skip,
            })

            pdf_batch = []
            failed = 0

            for i, inv in enumerate(invoices[skip:], skip + 1):
                try:
                    pdf_batch.append(self._render_invoice_pdf(inv))
                except Exception as e:
                    _logger.warning('Could not render %s: %s', inv.number, e)
                    failed += 1

                # Every _SAVE_BATCH: merge existing partial + current batch → new partial
                if len(pdf_batch) >= _SAVE_BATCH or i == total:
                    if pdf_batch:
                        existing = self._load_partial_bytes()
                        merged = self._merge_pdfs(
                            ([existing] if existing else []) + pdf_batch
                        )
                        self._save_partial(merged)
                        pdf_batch = []
                        # Commit both counters together after each partial save
                        self._commit({'processed_count': i, 'partial_count': i})
                        continue  # skip the progress-only commit below

                if i % _PROGRESS_BATCH == 0:
                    self._commit({'processed_count': i})

            # The partial attachment now holds the complete merged result
            final_bytes = self._load_partial_bytes()
            if not final_bytes:
                raise ValueError('No PDF was produced — all invoices failed to render.')

            doc_type = 'CreditNotes' if self.invoice_type_filter == 'out_refund' else 'Invoices'
            filename = '%s_%d.pdf' % (doc_type, target_year)

            if self.attachment_id:
                self.attachment_id.unlink()

            attachment = self.env['ir.attachment'].create({
                'name': filename,
                'type': 'binary',
                'datas': base64.b64encode(final_bytes),
                'datas_fname': filename,
                'res_model': self._name,
                'res_id': self.id,
                'mimetype': 'application/pdf',
            })

            self._delete_partial()

            msg = '%d document(s) merged.' % (total - failed)
            if failed:
                msg += ' %d skipped due to render errors.' % failed

            self._commit({
                'state': 'done',
                'attachment_id': attachment.id,
                'invoice_count': total - failed,
                'processed_count': total,
                'generated_on': fields.Datetime.now(),
                'error_message': msg if failed else False,
            })
            _logger.info('[%s]: %s', self.name, msg)
            return True

        except Exception as e:
            _logger.error('[%s] failed: %s', self.name, e)
            self._commit({'state': 'error', 'error_message': str(e)})
            return False

    # ── Control actions ─────────────────────────────────────────────────────

    def action_reset(self):
        """Full reset: delete partial, clear counters → fresh start."""
        self.ensure_one()
        self._delete_partial()
        self._commit({
            'state': 'draft',
            'total_count': 0,
            'processed_count': 0,
            'partial_count': 0,
            'started_on': False,
            'error_message': False,
        })

    @api.model
    def cron_reset_stuck_jobs(self):
        threshold = fields.Datetime.now() - timedelta(hours=_STUCK_HOURS)
        stuck = self.search([('state', '=', 'running'), ('started_on', '<', threshold)])
        for rec in stuck:
            _logger.warning('[%s] stuck since %s — marking as error.', rec.name, rec.started_on)
            rec._commit({
                'state': 'error',
                'error_message': (
                    'Job timed out after %d h — Odoo worker killed by resource limits '
                    '(limit_time_cpu / limit_memory_hard). '
                    'Progress saved up to invoice %d / %d. '
                    'Click Re-queue to resume from where it stopped.' % (
                        _STUCK_HOURS, rec.partial_count, rec.total_count)
                ),
            })

    @api.model
    def _bg_action_generate(self, record_id):
        """Entry point called by the one-shot background ir.cron (Odoo 9)."""
        self.browse(record_id).action_generate()

    def action_generate_background(self):
        """Schedule a one-shot ir.cron to run generation outside the HTTP request."""
        self.ensure_one()
        self._commit({
            'state': 'running',
            'started_on': fields.Datetime.now(),
        })
        self.env['ir.cron'].sudo().create({
            'name': 'Generate Yearly PDF: %s' % self.name,
            'model': self._name,
            'function': '_bg_action_generate',
            'args': repr((self.id,)),
            'interval_number': 1,
            'interval_type': 'minutes',
            'numbercall': 1,
            'nextcall': fields.Datetime.now(),
            'doall': False,
            'active': True,
            'user_id': self.env.uid,
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
