# -*- coding: utf-8 -*-
import base64
from datetime import date

from openerp import api, fields, models, _
from openerp.exceptions import UserError

POSTED_STATES = ('open', 'paid')


class WizardCreditNoteYearlyPdf(models.TransientModel):
    _name = 'wizard.credit.note.yearly.pdf'
    _description = 'Generate Yearly Credit Note PDF'

    year = fields.Integer(
        string='Year',
        required=True,
        default=lambda self: date.today().year - 1,
    )
    include_paid = fields.Boolean(
        string='Include Paid Credit Notes',
        default=True,
    )
    report_id = fields.Many2one(
        'ir.actions.report.xml',
        string='Print Template',
        required=True,
        domain="[('model', '=', 'account.invoice'), ('report_type', 'like', 'qweb')]",
        help='Template used to render each credit note.',
    )

    @api.model
    def default_get(self, fields_list):
        res = super(WizardCreditNoteYearlyPdf, self).default_get(fields_list)
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
        date_from = '%d-01-01' % self.year
        date_to = '%d-12-31' % self.year
        states = list(POSTED_STATES) if self.include_paid else ['open']

        credit_notes = self.env['account.invoice'].search([
            ('type', '=', 'out_refund'),
            ('state', 'in', states),
            ('date_invoice', '>=', date_from),
            ('date_invoice', '<=', date_to),
        ], order='date_invoice asc, number asc')

        if not credit_notes:
            raise UserError(_('No posted credit notes found for %d.') % self.year)

        generator = self.env['invoice.yearly.pdf']
        pdf_parts = []
        for cn in credit_notes:
            try:
                pdf_parts.append(self.env['report'].get_pdf(cn, self.report_id.report_name))
            except Exception:
                pass

        if not pdf_parts:
            raise UserError(_('None of the credit notes could be rendered.'))

        merged = generator._merge_pdfs(pdf_parts)
        filename = 'CreditNotes_%d.pdf' % self.year

        record_name = 'Credit Notes %d (Manual)' % self.year
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
