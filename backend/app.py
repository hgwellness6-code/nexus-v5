import os
import sys
import json
import csv
import io
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.database import init_db, get_conn
from backend.extractors.pdf_extractor import extract_text_from_pdf, extract_text_from_image, detect_doc_type
from backend.extractors.invoice_parser import parse_export_invoice
from backend.extractors.ups_parser import parse_ups_invoice
from backend.matchers.shipment_matcher import match_shipments
from backend.utils.organizer import organize_all_shipments, get_folder_tree
from backend.utils.reminder_engine import (
    create_reminders_for_shipment, get_upcoming_reminders, get_due_reminders,
    send_reminder_email, load_settings, save_settings, mark_reminder_sent
)
from backend.utils.chatbot import query_shipments
from backend.utils.analytics import (
    get_dashboard_stats, get_monthly_costs, get_cost_by_country,
    get_charge_composition, get_fuel_trend, get_recent_shipments,
    get_shipment_detail, get_alerts, search_shipments, get_destinations,
    get_cost_efficiency_report
)

def analyze_shipment_data(parsed, doc_type):
    insights = []

    fuel = float(parsed.get("fuel_surcharge") or 0)
    weight = float(parsed.get("billed_weight") or parsed.get("gross_weight") or 0)
    remote = float(parsed.get("remote_area_surcharge") or 0)
    total = float(parsed.get("total_charge") or parsed.get("declared_value") or 0)

    if fuel > 12:
        insights.append("⚠️ Fuel surcharge is higher than normal")
    if remote > 0:
        insights.append("⚠️ Remote area delivery increases cost")
    if weight > 100:
        insights.append("⚠️ Heavy shipment — consider splitting")
    if total > 500:
        insights.append("💰 High cost shipment")
    if fuel > 10 and remote > 0:
        insights.append("🚨 Cost spike: Fuel + Remote area combined")
    if weight > 100 and total > 500:
        insights.append("📦 Inefficient shipment: high weight & high cost")
    if total > 0 and weight > 0:
        cost_per_kg = total / weight
        if cost_per_kg > 10:
            insights.append(f"📊 Cost per kg is high: {round(cost_per_kg, 2)}")
    if parsed.get("destination_country") in ["USA", "UK", "Australia"]:
        insights.append("🌍 International priority zone shipment")

    return insights


UPLOAD_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'uploads')
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend')

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')
CORS(app)
os.makedirs(UPLOAD_DIR, exist_ok=True)
init_db()


# ── SERVE FRONTEND ────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


# ── UPLOAD ────────────────────────────────────────────────────────────────────
@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files['file']
    filename = f.filename
    filepath = os.path.join(UPLOAD_DIR, filename)
    f.save(filepath)

    ext = filename.lower().split('.')[-1]
    if ext == 'pdf':
        extracted = extract_text_from_pdf(filepath)
    elif ext in ('png', 'jpg', 'jpeg', 'tiff', 'bmp'):
        extracted = extract_text_from_image(filepath)
    else:
        return jsonify({"error": f"Unsupported file type: .{ext}"}), 400

    text = extracted.get('text', '')
    doc_type = detect_doc_type(text, filename)

    conn = get_conn()
    c = conn.cursor()
    c.execute(
        'INSERT INTO documents (filename, filepath, doc_type, raw_text) VALUES (?, ?, ?, ?)',
        (filename, filepath, doc_type, text[:50000])
    )
    doc_id = c.lastrowid

    parsed = {}
    if doc_type == 'export_invoice':
        parsed = parse_export_invoice(text)
        c.execute('''
            INSERT INTO export_invoices
                (document_id, invoice_number, invoice_date, consignee, destination_country,
                 tracking_id, gross_weight, chargeable_weight, declared_value,
                 currency, product_desc, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            doc_id, parsed.get('invoice_number'), parsed.get('invoice_date'),
            parsed.get('consignee'), parsed.get('destination_country'),
            parsed.get('tracking_id'), parsed.get('gross_weight'),
            parsed.get('chargeable_weight'), parsed.get('declared_value'),
            parsed.get('currency', 'USD'), parsed.get('product_desc'),
            parsed.get('confidence', 0)
        ))

    elif doc_type == 'ups_invoice':
        parsed = parse_ups_invoice(text)
        c.execute('''
            INSERT INTO ups_invoices
                (document_id, ups_invoice_number, invoice_date, tracking_number,
                 service_type, billed_weight, transport_charge, fuel_surcharge,
                 remote_area_surcharge, duty_tax, other_charges, total_charge,
                 currency, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            doc_id, parsed.get('ups_invoice_number'), parsed.get('invoice_date'),
            parsed.get('tracking_number'), parsed.get('service_type'),
            parsed.get('billed_weight'), parsed.get('transport_charge', 0),
            parsed.get('fuel_surcharge', 0), parsed.get('remote_area_surcharge', 0),
            parsed.get('duty_tax', 0), parsed.get('other_charges', 0),
            parsed.get('total_charge', 0), parsed.get('currency', 'USD'),
            parsed.get('confidence', 0)
        ))

    c.execute(
        'INSERT INTO audit_logs (action, entity_type, entity_id, details) VALUES (?, ?, ?, ?)',
        ('upload', 'document', doc_id, json.dumps({'filename': filename, 'doc_type': doc_type}))
    )

    conn.commit()
    conn.close()

    match_result = match_shipments()

    detected_currency = parsed.get('currency') if parsed else None
    if detected_currency:
        current_settings = load_settings()
        if current_settings.get('currency') != detected_currency:
            current_settings['currency'] = detected_currency
            save_settings(current_settings)

    return jsonify({
        "success": True,
        "document_id": doc_id,
        "doc_type": doc_type,
        "method": extracted.get('method'),
        "pages": extracted.get('pages'),
        "parsed": parsed,
        "match_result": match_result,
        "detected_currency": detected_currency
    })


# ── DOCUMENTS ─────────────────────────────────────────────────────────────────
@app.route('/api/documents', methods=['GET'])
def list_documents():
    conn = get_conn()
    rows = conn.execute(
        'SELECT id, filename, doc_type, created_at FROM documents ORDER BY created_at DESC'
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/documents/<int:did>', methods=['DELETE'])
def delete_document(did):
    conn = get_conn()
    row = conn.execute('SELECT filepath FROM documents WHERE id = ?', (did,)).fetchone()
    if row:
        try:
            if os.path.exists(row['filepath']):
                os.remove(row['filepath'])
        except Exception:
            pass

        exp_ids = [r[0] for r in conn.execute(
            'SELECT id FROM export_invoices WHERE document_id = ?', (did,)).fetchall()]
        ups_ids = [r[0] for r in conn.execute(
            'SELECT id FROM ups_invoices WHERE document_id = ?', (did,)).fetchall()]

        shipment_ids = []
        if exp_ids:
            placeholders = ','.join('?' * len(exp_ids))
            shipment_ids += [r[0] for r in conn.execute(
                f'SELECT id FROM shipments WHERE export_invoice_id IN ({placeholders})', exp_ids).fetchall()]
        if ups_ids:
            placeholders = ','.join('?' * len(ups_ids))
            shipment_ids += [r[0] for r in conn.execute(
                f'SELECT id FROM shipments WHERE ups_invoice_id IN ({placeholders})', ups_ids).fetchall()]

        if shipment_ids:
            placeholders = ','.join('?' * len(shipment_ids))
            conn.execute(f'DELETE FROM reminders WHERE shipment_id IN ({placeholders})', shipment_ids)
            conn.execute(f'DELETE FROM shipments WHERE id IN ({placeholders})', shipment_ids)

        if exp_ids:
            placeholders = ','.join('?' * len(exp_ids))
            conn.execute(f'DELETE FROM export_invoices WHERE id IN ({placeholders})', exp_ids)
        if ups_ids:
            placeholders = ','.join('?' * len(ups_ids))
            conn.execute(f'DELETE FROM ups_invoices WHERE id IN ({placeholders})', ups_ids)

        conn.execute('DELETE FROM documents WHERE id = ?', (did,))
        conn.commit()
    conn.close()
    return jsonify({"success": True})


# ── SHIPMENTS ─────────────────────────────────────────────────────────────────
@app.route('/api/shipments', methods=['GET'])
def list_shipments():
    query = request.args.get('q', '')
    status = request.args.get('status')
    dest = request.args.get('destination')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    min_cost = request.args.get('min_cost', type=float)
    max_cost = request.args.get('max_cost', type=float)
    limit = request.args.get('limit', 50, type=int)

    rows = search_shipments(query, status, dest, date_from, date_to, min_cost, max_cost, limit)
    return jsonify(rows)


@app.route('/add_shipment', methods=['POST'])
def add_shipment():
    shipment = {
        "transport": request.form.get("transport"),
        "weight": float(request.form.get("weight")),
        "location": request.form.get("location"),
        "fuel_surcharge": float(request.form.get("fuel")),
        "total": float(request.form.get("total")),
        "date": request.form.get("date")
    }
    insights = analyze_shipment_data(shipment, None)
    return jsonify({
        "shipment": shipment,
        "insights": insights
    })


@app.route('/api/shipments/<int:sid>', methods=['GET'])
def get_shipment(sid):
    detail = get_shipment_detail(sid)
    if not detail:
        return jsonify({"error": "Not found"}), 404
    return jsonify(detail)


@app.route('/api/shipments/<int:sid>/notes', methods=['POST'])
def update_notes(sid):
    data = request.json
    conn = get_conn()
    conn.execute('UPDATE shipments SET notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                 (data.get('notes', ''), sid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/shipments/<int:sid>/tags', methods=['POST'])
def update_tags(sid):
    data = request.json
    conn = get_conn()
    conn.execute('UPDATE shipments SET tags = ? WHERE id = ?', (data.get('tags', ''), sid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/shipments/<int:sid>/priority', methods=['POST'])
def update_priority(sid):
    data = request.json
    conn = get_conn()
    conn.execute('UPDATE shipments SET priority = ? WHERE id = ?', (data.get('priority', 'normal'), sid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/shipments/bulk', methods=['POST'])
def bulk_action():
    data = request.json
    action = data.get('action')
    ids = data.get('ids', [])
    if not ids:
        return jsonify({"error": "No IDs provided"}), 400

    conn = get_conn()
    placeholders = ','.join('?' * len(ids))
    if action == 'delete':
        conn.execute(f'DELETE FROM shipments WHERE id IN ({placeholders})', ids)
    elif action == 'tag':
        tag = data.get('tag', '')
        for sid in ids:
            conn.execute(
                "UPDATE shipments SET tags = CASE WHEN tags='' THEN ? ELSE tags||','||? END WHERE id=?",
                (tag, tag, sid)
            )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "affected": len(ids)})


@app.route('/api/match', methods=['POST'])
def run_match():
    result = match_shipments()
    organize_all_shipments()
    return jsonify(result)


# ── ANALYTICS ─────────────────────────────────────────────────────────────────
@app.route('/api/analytics/dashboard', methods=['GET'])
def dashboard():
    return jsonify({
        "stats": get_dashboard_stats(),
        "alerts": get_alerts(),
        "recent": get_recent_shipments(10)
    })


@app.route('/api/analytics/monthly', methods=['GET'])
def monthly():
    months = request.args.get('months', 12, type=int)
    return jsonify(get_monthly_costs(months))


@app.route('/api/analytics/countries', methods=['GET'])
def countries():
    return jsonify(get_cost_by_country())


@app.route('/api/analytics/charges', methods=['GET'])
def charges():
    return jsonify(get_charge_composition())


@app.route('/api/analytics/fuel', methods=['GET'])
def fuel():
    return jsonify(get_fuel_trend())


@app.route('/api/analytics/efficiency', methods=['GET'])
def efficiency():
    return jsonify(get_cost_efficiency_report())


@app.route('/api/analytics/destinations', methods=['GET'])
def destinations():
    return jsonify(get_destinations())


# ── CHATBOT ───────────────────────────────────────────────────────────────────
@app.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    query = data.get('query', '').strip()
    if not query:
        return jsonify({"answer": "Please ask a question.", "data": []}), 400
    result = query_shipments(query)
    return jsonify(result)


# ── REMINDERS ─────────────────────────────────────────────────────────────────
@app.route('/api/reminders', methods=['GET'])
def list_reminders():
    return jsonify({
        "upcoming": get_upcoming_reminders(60),
        "due": get_due_reminders()
    })


@app.route('/api/reminders/templates', methods=['GET'])
def reminder_templates():
    conn = get_conn()
    rows = conn.execute('SELECT * FROM reminder_templates').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/reminders/templates/<int:tid>', methods=['PATCH'])
def update_template(tid):
    data = request.json
    conn = get_conn()
    if 'is_enabled' in data:
        conn.execute('UPDATE reminder_templates SET is_enabled = ? WHERE id = ?',
                     (1 if data['is_enabled'] else 0, tid))
    if 'days_after' in data:
        conn.execute('UPDATE reminder_templates SET days_after = ? WHERE id = ?',
                     (data['days_after'], tid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/reminders/<int:rid>/send', methods=['POST'])
def send_reminder(rid):
    result = send_reminder_email(rid)
    return jsonify(result)


@app.route('/api/reminders/<int:rid>/mark-sent', methods=['POST'])
def mark_sent(rid):
    mark_reminder_sent(rid)
    return jsonify({"success": True})


# ── SETTINGS ──────────────────────────────────────────────────────────────────
@app.route('/api/settings', methods=['GET'])
def get_settings():
    s = load_settings()
    s.pop('email_password', None)
    return jsonify(s)


@app.route('/api/settings', methods=['POST'])
def update_settings():
    data = request.json
    current = load_settings()
    current.update(data)
    save_settings(current)
    return jsonify({"success": True})


# ── FILES & EXPORT ────────────────────────────────────────────────────────────
@app.route('/api/folders', methods=['GET'])
def folders():
    return jsonify(get_folder_tree())


@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    rows = get_recent_shipments(10000)
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("tracking_id,export_invoice,date,destination,weight_kg,transport,fuel_surcharge,remote_area,total,status\n")
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={"Content-Disposition": "attachment; filename=nexus_shipments.csv"}
    )


@app.route('/api/export/json', methods=['GET'])
def export_json():
    rows = get_recent_shipments(10000)
    return Response(
        json.dumps(rows, indent=2),
        mimetype='application/json',
        headers={"Content-Disposition": "attachment; filename=nexus_shipments.json"}
    )


# ── AUDIT LOG ─────────────────────────────────────────────────────────────────
@app.route('/api/audit', methods=['GET'])
def audit_log():
    conn = get_conn()
    rows = conn.execute(
        'SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 100'
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── SAVED SEARCHES ────────────────────────────────────────────────────────────
@app.route('/api/saved-searches', methods=['GET'])
def list_saved_searches():
    conn = get_conn()
    rows = conn.execute('SELECT * FROM saved_searches ORDER BY created_at DESC').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/saved-searches', methods=['POST'])
def create_saved_search():
    data = request.json
    conn = get_conn()
    conn.execute('INSERT INTO saved_searches (name, query) VALUES (?, ?)',
                 (data.get('name'), data.get('query')))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/saved-searches/<int:sid>', methods=['DELETE'])
def delete_saved_search(sid):
    conn = get_conn()
    conn.execute('DELETE FROM saved_searches WHERE id = ?', (sid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/export/pdf')
def export_pdf():
    from backend.utils.pdf_report import generate_report
    from datetime import datetime as _dt

    date_from = request.args.get('date_from')
    date_to   = request.args.get('date_to')
    period_label = request.args.get('period', 'All Time')

    stats      = get_dashboard_stats()
    shipments  = search_shipments('', date_from=date_from, date_to=date_to, limit=200)
    monthly    = get_monthly_costs(24)
    countries  = get_cost_by_country()
    charges    = get_charge_composition()
    fuel_trend = get_fuel_trend(12)
    efficiency = get_cost_efficiency_report()
    alerts     = get_alerts()

    pdf_bytes = generate_report(
        stats=stats,
        shipments=shipments,
        monthly=monthly,
        countries=countries,
        charges=charges,
        fuel_trend=fuel_trend,
        efficiency=efficiency,
        alerts=alerts,
        period_label=period_label,
        title="Nexus Shipping Intelligence Report",
    )

    filename = f"nexus-report-{_dt.now().strftime('%Y%m%d-%H%M')}.pdf"
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


# ── NEW v5 ROUTES ──────────────────────────────────────────────────────────────

@app.route('/api/analytics/weight-distribution', methods=['GET'])
def weight_distribution():
    conn = get_conn()
    c = conn.cursor()
    buckets = [
        ('0–5 kg', 0, 5), ('5–10 kg', 5, 10), ('10–20 kg', 10, 20),
        ('20–50 kg', 20, 50), ('50–100 kg', 50, 100), ('100+ kg', 100, 9999)
    ]
    result = []
    for label, lo, hi in buckets:
        row = c.execute(
            'SELECT COUNT(*) as cnt FROM shipments WHERE gross_weight >= ? AND gross_weight < ?',
            (lo, hi)
        ).fetchone()
        result.append({'label': label, 'count': row['cnt'] if row else 0})
    conn.close()
    return jsonify(result)


@app.route('/api/analytics/service-mix', methods=['GET'])
def service_mix():
    conn = get_conn()
    rows = conn.execute('''
        SELECT COALESCE(ui.service_type, 'Unknown') as service,
               COUNT(*) as count,
               ROUND(SUM(ui.total_charge), 2) as total
        FROM shipments s
        JOIN ups_invoices ui ON s.ups_invoice_id = ui.id
        GROUP BY service ORDER BY count DESC
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/analytics/timeline', methods=['GET'])
def shipment_timeline():
    conn = get_conn()
    rows = conn.execute('''
        SELECT strftime('%Y-W%W', ship_date) as week,
               COUNT(*) as count,
               ROUND(SUM(total_cost), 2) as total
        FROM shipments
        WHERE ship_date >= date('now', '-52 weeks')
        GROUP BY week ORDER BY week ASC
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/analytics/top-consignees', methods=['GET'])
def top_consignees():
    conn = get_conn()
    rows = conn.execute('''
        SELECT consignee,
               COUNT(*) as count,
               ROUND(SUM(total_cost), 2) as total_spend,
               ROUND(AVG(total_cost), 2) as avg_cost
        FROM shipments
        WHERE consignee IS NOT NULL AND consignee != ''
        GROUP BY consignee ORDER BY total_spend DESC LIMIT 10
    ''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/shipments/<int:sid>/status', methods=['POST'])
def update_status(sid):
    data = request.json
    conn = get_conn()
    conn.execute('UPDATE shipments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                 (data.get('status', 'pending'), sid))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/api/analytics/compare', methods=['GET'])
def compare_periods():
    p1_from = request.args.get('p1_from', '')
    p1_to   = request.args.get('p1_to', '')
    p2_from = request.args.get('p2_from', '')
    p2_to   = request.args.get('p2_to', '')

    def period_stats(date_from, date_to):
        conn = get_conn()
        c = conn.cursor()
        sql = 'SELECT COUNT(*) as cnt, ROUND(SUM(total_cost),2) as spend, ROUND(AVG(cost_per_kg),2) as avg_per_kg FROM shipments WHERE 1=1'
        params = []
        if date_from: sql += ' AND ship_date >= ?'; params.append(date_from)
        if date_to:   sql += ' AND ship_date <= ?'; params.append(date_to)
        row = c.execute(sql, params).fetchone()
        conn.close()
        return dict(row) if row else {}

    return jsonify({
        'period1': period_stats(p1_from, p1_to),
        'period2': period_stats(p2_from, p2_to),
    })


if __name__ == '__main__':
    init_db()
    print("\n" + "="*55)
    print("  NEXUS Shipping Intelligence  v2.0")
    print("  Running at http://0.0.0.0:5000")
    print("="*55 + "\n")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
