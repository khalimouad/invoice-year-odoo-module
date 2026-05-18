# Invoice Yearly PDF Export — Odoo Module

Generates a **single merged PDF** containing every posted invoice for a chosen fiscal year (January 1 – December 31) and stores it as an `ir.attachment`.

Compatible with **Odoo 9** and **Odoo 13**.

---

## Features

| Feature | Detail |
|---|---|
| Year picker | Choose any year via the wizard or let the cron handle it |
| Invoice type | Customer Invoices, Credit Notes, or both |
| QWeb template | Configurable report reference (default: `account.report_invoice`) |
| Merging | Uses **pypdf** (or **PyPDF2** as fallback) to merge per-invoice PDFs |
| Storage | Saved as `ir.attachment` on the `invoice.yearly.pdf` record |
| Cron | Runs automatically on Jan 1st each year for the previous year |

---

## Installation

### 1. Install the Python PDF library

```bash
pip install pypdf          # Odoo 13+ (Python 3)
# or
pip install PyPDF2         # Odoo 9 (Python 2/3)
```

### 2. Copy the module

```
<odoo-addons-path>/
└── invoice_yearly_pdf/
```

### 3. Activate

```
Odoo → Settings → Apps → search "Invoice Yearly PDF" → Install
```

---

## Usage

### Manual generation (Wizard)

1. Go to **Accounting → Yearly PDF Export → Generate PDF for a Year**
2. Pick the **Year**, **Invoice Type**, and optionally adjust the **QWeb Report Reference**
3. Click **Generate & Download PDF** — the browser downloads the merged PDF immediately

### Automated cron

The cron `Generate Yearly Invoice PDF` is installed and active by default.  
It runs on **January 1st** each year and produces a PDF for the *previous* year.

To trigger it manually:

```
Settings → Technical → Scheduled Actions → Generate Yearly Invoice PDF → Run Manually
```

### History

All generated PDFs (manual + cron) are listed under:  
**Accounting → Yearly PDF Export → Generated PDFs History**

---

## QWeb Report Reference

| Odoo version | Default report ref |
|---|---|
| Odoo 13 | `account.report_invoice` |
| Odoo 9  | `account.report_invoice_with_payments` |

To use a **custom template**, enter its external ID in the wizard's *QWeb Report Reference* field.

---

## File Structure

```
invoice_yearly_pdf/
├── __manifest__.py
├── __init__.py
├── models/
│   ├── __init__.py
│   └── invoice_yearly_pdf.py   ← core logic + cron method
├── wizard/
│   ├── __init__.py
│   └── wizard_invoice_yearly_pdf.py  ← user-facing wizard
├── views/
│   ├── wizard_views.xml
│   └── menu.xml
├── security/
│   └── ir.model.access.csv
└── data/
    └── cron.xml
```
