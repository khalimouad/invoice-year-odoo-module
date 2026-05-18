# -*- coding: utf-8 -*-
import base64
import io
import logging
import os
import subprocess
from datetime import date, timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

_RENDER_BATCH = 20     # invoices rendered in one wkhtmltopdf call (same as manual print)
_STUCK_HOURS = 2


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
    started_on = fields.Datetime(string='Started On', readonly=True)
    total_count = fields.Integer(string='Total Documents', readonly=True)
    processed_count = fields.Integer(string='Processed', readonly=True)
    # Last invoice index saved into the partial PDF (resume point)
    partial_count = fields.Integer(string='Partial Saved At', readonly=True)
    progress_pct = fields.Integer(
        string='Progress', compute='_compute_progress', store=False)
    is_stuck = fields.Boolean(
        string='Stuck?', compute='_compute_progress', store=False)

    invoice_count = fields.Integer(string='Documents Merged', readonly=True)
    error_message = fields.Text(string='Error / Notes', readonly=True)
    generated_on = fields.Datetime(string='Generated On', readonly=True)

    @api.depends('processed_count', 'total_count', 'state', 'started_on')
    def _compute_progress(self):
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

    def _render_batch_pdf(self, invoices):
        """Render a recordset of invoices in one wkhtmltopdf call (identical to manual print)."""
        report = self.report_id or self.env.ref('account.account_invoices')
        # Use the local HTTP address so wkhtmltopdf can always reach the server.
        # web.base.url is often an HTTPS external URL that the server itself cannot
        # reach (NAT, self-signed cert) causing silent CSS loss in cron context.
        ICP = self.env['ir.config_parameter'].sudo()
        base_url = ICP.get_param('report.url')
        if not base_url:
            import odoo
            port = odoo.tools.config.get('http_port') or odoo.tools.config.get('xmlrpc_port') or 8069
            base_url = 'http://127.0.0.1:%d' % port
        pdf, _ct = report.with_context(base_url=base_url).render_qweb_pdf(invoices.ids)
        return pdf

    @api.model
    def _merge_pdfs(self, pdf_list):
        try:
            from pypdf import PdfWriter as _Merger
        except ImportError:
            try:
                from PyPDF2 import PdfMerger as _Merger
            except ImportError:
                from PyPDF2 import PdfFileMerger as _Merger

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

            failed = 0
            remaining = invoices[skip:]

            for batch_offset in range(0, len(remaining), _RENDER_BATCH):
                batch = remaining[batch_offset:batch_offset + _RENDER_BATCH]
                batch_end = skip + batch_offset + len(batch)

                try:
                    # Single wkhtmltopdf call for the whole batch — same as manual print
                    pdf = self._render_batch_pdf(batch)
                    batch_pdfs = [pdf]
                    batch_failed = 0
                except Exception as e:
                    _logger.warning('Batch %d-%d failed (%s), retrying one by one',
                                    skip + batch_offset + 1, batch_end, e)
                    # Fall back to one-by-one so a single bad invoice doesn't lose the batch
                    batch_pdfs = []
                    batch_failed = 0
                    for inv in batch:
                        try:
                            batch_pdfs.append(self._render_batch_pdf(inv))
                        except Exception as e2:
                            _logger.warning('Could not render %s: %s', inv.name, e2)
                            batch_failed += 1
                    failed += batch_failed

                if batch_pdfs:
                    existing = self._load_partial_bytes()
                    merged = self._merge_pdfs(([existing] if existing else []) + batch_pdfs)
                    self._save_partial(merged)

                self._commit({'processed_count': batch_end, 'partial_count': batch_end})

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
                'res_model': self._name,
                'res_id': self.id,
                'mimetype': 'application/pdf',
            })

            self._delete_partial()

            merged_count = total - failed
            msg = '%d document(s) merged.' % merged_count
            if failed:
                msg += ' %d skipped due to render errors.' % failed

            self._commit({
                'state': 'done',
                'attachment_id': attachment.id,
                'invoice_count': merged_count,
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

    def action_generate_background(self):
        """Schedule a one-shot ir.cron to run outside the HTTP request."""
        self.ensure_one()
        self._commit({
            'state': 'running',
            'started_on': fields.Datetime.now(),
        })
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
    def cron_auto_update(self):
        """Pull latest commits from origin and restart Odoo if anything changed.

        Requires a passwordless sudoers entry for the odoo system user:
            odoo ALL=(ALL) NOPASSWD: /usr/sbin/service odoo-server restart
        """
        # The git repo is the parent of the module directory
        module_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo_dir = os.path.dirname(module_dir)
        if not os.path.isdir(os.path.join(repo_dir, '.git')):
            _logger.warning('[auto-update] %s is not a git repo, skipping.', repo_dir)
            return
        try:
            subprocess.run(['git', '-C', repo_dir, 'fetch', '--quiet'],
                           check=True, capture_output=True, timeout=30)
            result = subprocess.run(
                ['git', '-C', repo_dir, 'rev-list', '--count', 'HEAD..@{u}'],
                check=True, capture_output=True, text=True, timeout=10)
            ahead = int((result.stdout or '0').strip())
            if ahead == 0:
                return
            _logger.info('[auto-update] %d new commit(s) on origin, pulling...', ahead)
            subprocess.run(['git', '-C', repo_dir, 'pull', '--ff-only', '--quiet'],
                           check=True, capture_output=True, timeout=60)
            _logger.info('[auto-update] Pull OK, scheduling Odoo restart.')
            # Detached so the cron transaction can commit before the service goes down
            subprocess.Popen(
                ['/bin/sh', '-c', 'sleep 5 && sudo /usr/sbin/service odoo-server restart'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except subprocess.CalledProcessError as e:
            _logger.warning('[auto-update] git command failed: %s',
                            (e.stderr or b'').decode('utf-8', 'replace'))
        except Exception as e:
            _logger.warning('[auto-update] failed: %s', e)

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
