# Nexus Shipping Intelligence — v5

## What's New in v5

### New Features
- **Dark Mode** — Toggle via button in topbar or `Alt+N`
- **Period Comparison** — Compare any two date ranges side-by-side with overlay chart
- **Top Consignees** — Ranked table + horizontal bar chart of spending by recipient
- **Weight Distribution** — Histogram bucketing shipments by gross weight
- **Activity Heatmap** — 52-week shipment activity calendar on Dashboard
- **Service Mix Chart** — UPS service type breakdown (Express / Expedited / Standard)
- **Notification Bell** — Quick-access badge for due reminders
- **Keyboard Shortcuts** — `Alt+D` Dashboard, `Alt+S` Shipments, `Alt+A` Analytics, `Alt+I` Import, `Alt+C` Chat, `Alt+R` Reminders, `Alt+P` PDF, `Alt+E` Estimator, `Alt+K` Compare, `/` Search
- **Export Excel** — Additional export button alongside CSV/JSON
- **5 New API Endpoints** — Weight distribution, service mix, timeline, top consignees, period compare, status update

### New API Endpoints (v5)
| Endpoint | Description |
|---|---|
| `GET /api/analytics/weight-distribution` | Shipment weight buckets |
| `GET /api/analytics/service-mix` | UPS service type breakdown |
| `GET /api/analytics/timeline` | Weekly shipment activity (52 weeks) |
| `GET /api/analytics/top-consignees` | Top 10 consignees by spend |
| `GET /api/analytics/compare` | Compare two period stats |
| `POST /api/shipments/<id>/status` | Update shipment status |

## Setup

### Requirements
```
pip install -r requirements.txt
```

### Run
```
python -m backend.app
```
Or double-click `START_NEXUS.bat` on Windows.

### Access
Open: http://localhost:5000

## Tech Stack
- **Backend**: Flask + SQLite
- **Frontend**: Vanilla JS + Chart.js
- **PDF**: ReportLab
- **OCR**: PyMuPDF + pdfplumber + Tesseract
- **No external APIs required**
