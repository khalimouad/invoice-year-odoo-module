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
    report_id = fields.Many2one(
        'ir.actions.report',
        string='Print Template',
        required=True,
        domain="[('model', '=', 'account.move'), ('report_type', 'like', 'qweb')]",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        default_report = self.env.ref('account.account_invoices', raise_if_not_found=False)
        if not default_report:
            default_report = self.env['ir.actions.report'].search([
                ('model', '=', 'account.move'),
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

    # ── Synchronous (small datasets) ────────────────────────────────────────

    def action_generate_pdf(self):
        self.ensure_one()
        date_from = date(self.year, 1, 1)
        date_to = date(self.year, 12, 31)
        types = ('out_invoice', 'out_refund') if self.invoice_type == 'both' else (self.invoice_type,)

        invoices = self.env['account.move'].search([
            ('type', 'in', types),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
        ], order='invoice_date asc, name asc')

        if not invoices:
            raise UserError(_('No posted invoices found for %d with the selected type.') % self.year)

        generator = self.env['invoice.yearly.pdf']
        pdf_parts = []
        for inv in invoices:
            try:
                pdf, _ct = self.report_id.render_qweb_pdf(inv.ids)
                pdf_parts.append(pdf)
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
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Generation queued'),
                'message': _(
                    'The PDF is being generated in the background. '
                    'Open "Generated PDFs History" to download it once ready.'
                ),
                'type': 'info',
                'sticky': True,
                'next': {
                    'type': 'ir.actions.act_window',
                    'res_model': 'invoice.yearly.pdf',
                    'view_mode': 'tree,form',
                    'target': 'current',
                },
            },
        }
