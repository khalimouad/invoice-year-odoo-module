# Invoice Yearly PDF Export — Odoo Modules

Two independent modules — one per Odoo major version.

| Module folder | Target version |
|---|---|
| `invoice_yearly_pdf_v13/` | Odoo 13 (and 14/15/16 with minor adjustments) |
| `invoice_yearly_pdf_v9/` | Odoo 9 |

---

## Features

- **Year picker** — choose any year in the wizard
- **Invoice type filter** — Customer Invoices, Credit Notes, or both
- **Print template picker** — dropdown showing every QWeb report available for invoices (same list as the Print button on the invoice list)
- **Merged PDF** — all matching invoices rendered individually then merged into a single PDF
- **Attachment storage** — saved as `ir.attachment`, downloadable from the history view
- **Cron** — runs automatically on January 1st each year for the previous year

---

## Installation

### 1 — Install the PDF merge library

**Odoo 13 (Python 3)**
```bash
pip install pypdf
# PyPDF2 works as fallback if pypdf is not available
```

**Odoo 9 (Python 2 or 3)**
```bash
pip install PyPDF2
```

### 2 — Copy the correct module to your addons path

```
<your-addons>/
├── invoice_yearly_pdf_v13/   ← for Odoo 13
└── invoice_yearly_pdf_v9/    ← for Odoo 9
```

### 3 — Install from the Apps menu

Search for **"Invoice Yearly PDF"** and install.

---

## Usage

### Manual — Wizard

**Accounting → Yearly PDF Export → Generate PDF for a Year**

| Field | Description |
|---|---|
| Year | Fiscal year to export (Jan 1 – Dec 31) |
| Invoice Type | Customer Invoices / Credit Notes / Both |
| Include Paid *(v9 only)* | Toggle to include paid invoices in addition to open ones |
| Print Template | Dropdown of all QWeb report templates for invoices |

Click **Generate & Download PDF** → browser downloads the merged PDF immediately.

### Automatic — Cron

The scheduled action **"Generate Yearly Invoice PDF"** runs on **January 1st** of every year and produces a PDF for the *previous* year using the standard invoice template.

Trigger manually: `Settings → Technical → Scheduled Actions → Generate Yearly Invoice PDF → Run Manually`

### History

**Accounting → Yearly PDF Export → Generated PDFs History** — lists every generated PDF with its status and download link.

---

## Key differences between versions

| | Odoo 13 module | Odoo 9 module |
|---|---|---|
| Invoice model | `account.move` | `account.invoice` |
| Posted state | `state = 'posted'` | `state in ('open', 'paid')` |
| Report model | `ir.actions.report` | `ir.actions.report.xml` |
| Render API | `report._render_qweb_pdf()` | `env['report'].get_pdf()` |
| XML root tag | `<odoo>` | `<openerp>` |
| Attachment field | no `datas_fname` | requires `datas_fname` |
