import os
import re
import json
import threading
from datetime import datetime
import calendar
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request, jsonify
from dateutil import parser
import pytz
from fpdf import FPDF
import tempfile
import random
import matplotlib
matplotlib.use('Agg') # សម្រាប់ដំណើរការលើ Server ដោយគ្មាន screen
import matplotlib.pyplot as plt

app = Flask(__name__)

# --- ការកំណត់ Environment Variables ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
TELEGRAM_TOPIC_ID = os.getenv('TELEGRAM_TOPIC_ID', '') 

SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '')
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON', '')

TARGET_PAGES = ['Main Page', 'Sovanna']
tz = pytz.timezone('Asia/Phnom_Penh')

report_cache = {}
CACHE_VERSION = 1

# ==========================================
# Telegram APIs
# ==========================================
def telegram_api(method, payload, is_multipart=False):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    if TELEGRAM_TOPIC_ID:
        if is_multipart: payload['data']['message_thread_id'] = TELEGRAM_TOPIC_ID
        else: payload['message_thread_id'] = TELEGRAM_TOPIC_ID
    try:
        if is_multipart: 
            return requests.post(url, files=payload['files'], data=payload['data'], timeout=30)
        else: 
            return requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram API Error ({method}):", e)
        return None

def send_simple_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup: payload["reply_markup"] = reply_markup
    return telegram_api("sendMessage", payload)

def delete_message(chat_id, message_id):
    if not message_id: return
    payload = {"chat_id": chat_id, "message_id": message_id}
    telegram_api("deleteMessage", payload)

def send_document(chat_id, file_path, caption):
    with open(file_path, 'rb') as f:
        payload = {
            'files': {'document': (os.path.basename(file_path), f, 'application/pdf')},
            'data': {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
        }
        return telegram_api("sendDocument", payload, is_multipart=True)

# ==========================================
# Google Sheets & Data Logic
# ==========================================
def get_google_sheet():
    if not GOOGLE_CREDENTIALS_JSON: return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID)
    except: return None

def fetch_report_data(target_date, is_monthly=False):
    ss = get_google_sheet()
    if not ss: return None, False
    today_month_search, today_for_search = target_date.strftime("%Y-%m"), target_date.strftime("%Y-%m-%d")
    target_y, target_m, target_d = target_date.year, target_date.month, target_date.day
    has_data = False
    pages_data = []

    for page_name in TARGET_PAGES:
        try:
            worksheet = ss.worksheet(page_name)
            data = worksheet.get_all_values()
        except: continue
        if len(data) <= 4: continue
        
        target_string = str(data[2][0]) if len(data[2]) > 0 else "0"
        match = re.search(r'\d+(\.\d+)?', target_string)
        target_amount = float(match.group(0)) if match else 0.0
        
        num_chat = online_booking = visit = close_deal = package_count = 0
        total_sale_today = total_sale_monthly = 0.0
        
        for i in range(4, len(data)):
            row = data[i]
            if len(row) < 2 or not row[1]: continue
            str_date = str(row[1]).strip()
            is_match_day = is_match_month = False
            
            try:
                p_date = parser.parse(str_date)
                if p_date.year == target_y and p_date.month == target_m:
                    is_match_month = True
                    if p_date.day == target_d: is_match_day = True
            except:
                if str_date.startswith(today_month_search): is_match_month = True
                if str_date == today_for_search: is_match_day = True

            row_match = is_match_month if is_monthly else is_match_day

            if is_match_month:
                try: total_sale_monthly += float(row[7].replace('$', '').replace(',', '') or 0)
                except: pass
            
            if row_match:
                num_chat += 1
                try: total_sale_today += float(row[7].replace('$', '').replace(',', '') or 0)
                except: pass
                if len(row) > 9 and str(row[9]) in ['1', 'TRUE', 'true']: online_booking += 1
                if len(row) > 10 and str(row[10]) in ['1', 'TRUE', 'true']: visit += 1
                if len(row) > 11 and str(row[11]) in ['1', 'TRUE', 'true']: package_count += 1
                if len(row) > 12 and str(row[12]) in ['1', 'TRUE', 'true']: close_deal += 1
        
        if num_chat > 0 or total_sale_monthly > 0:
            has_data = True
            rate_booking = f"{(online_booking / num_chat) * 100:.2f}" if num_chat > 0 else "0.00"
            rate_visit = f"{(visit / num_chat) * 100:.2f}" if num_chat > 0 else "0.00"
            rate_close_deal = f"{(close_deal / num_chat) * 100:.2f}" if num_chat > 0 else "0.00"
            rate_package = f"{(package_count / close_deal) * 100:.2f}" if close_deal > 0 else "0.00"
            rate_sale = f"{(total_sale_monthly / target_amount) * 100:.2f}" if target_amount > 0 else "0.00"
            
            pages_data.append({
                "page_name": page_name, "num_chat": num_chat, "online_booking": online_booking, 
                "visit": visit, "close_deal": close_deal, "package_count": package_count,
                "total_sale_today": total_sale_today, "total_sale_monthly": total_sale_monthly,
                "target_amount": target_amount, "rate_booking": rate_booking, 
                "rate_visit": rate_visit, "rate_close_deal": rate_close_deal, 
                "rate_package": rate_package, "rate_sale": rate_sale
            })
            
    return {"display_date": target_date.strftime("%B %Y") if is_monthly else target_date.strftime("%d/%m/%Y"), 
            "search_key": today_month_search if is_monthly else today_for_search,
            "has_data": has_data, "pages": pages_data}, True

# ==========================================
# Generate PDF (Dashboard HLCC)
# ==========================================
def generate_and_send_pdf(requested_date_str, target_chat_id, is_monthly=False, loading_msg_id=None):
    try:
        try: target_date = parser.parse(requested_date_str)
        except: return
        report_data, is_success = fetch_report_data(target_date, is_monthly)
        if not is_success or not report_data['has_data']:
            send_simple_message(target_chat_id, "📭 មិនមានទិន្នន័យសម្រាប់ Export PDF ទេ។")
            return
        
        pdf = FPDF(orientation='P', unit='mm', format='A4')
        pdf.add_page()
        hlcc_blue, hlcc_grey, petal_pink = (52, 157, 216), (80, 80, 80), (255, 220, 230)
        logo_path = 'logo.png'

        with pdf.local_context(fill_opacity=0.06):
            pdf.set_fill_color(*petal_pink)
            random.seed(42)
            for _ in range(20):
                x, y, w = random.randint(10, 190), random.randint(10, 280), random.randint(10, 25)
                pdf.ellipse(x, y, w, w * 0.6, style="F")

        if os.path.exists(logo_path):
            with pdf.local_context(fill_opacity=0.15):
                pdf.image(logo_path, x=30, y=70, w=150)
            pdf.image(logo_path, x=10, y=8, w=35)
            pdf.set_x(50)
        else: pdf.set_x(10)

        pdf.set_font("Helvetica", "B", 20); pdf.set_text_color(*hlcc_blue)
        pdf.cell(0, 10, "HLCC INNOVATIVE BEAUTY CENTER", ln=True, align="L")
        pdf.set_x(pdf.get_x() + 40 if os.path.exists(logo_path) else 10)
        pdf.set_font("Helvetica", "B", 12); pdf.set_text_color(*hlcc_grey)
        title = f"{'Monthly' if is_monthly else 'Daily'} Sales Dashboard - {report_data['display_date']}"
        pdf.cell(0, 7, title, ln=True, align="L")
        pdf.ln(5)

        # ក្រាប Achievement
        achieved_pct = [float(p['rate_sale']) for p in report_data['pages']]
        page_names = [p['page_name'].replace(" Page", "") for p in report_data['pages']]
        plt.figure(figsize=(6, 3))
        plt.bar(page_names, achieved_pct, color='#349dd8')
        plt.ylabel('Achieved (%)', fontsize=10); plt.title('Sales Achievement by Page', fontsize=12)
        plt.ylim(0, max(max(achieved_pct) + 10, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmpfile:
            plt.savefig(tmpfile.name, format='png', bbox_inches='tight', dpi=100); chart_path = tmpfile.name
        plt.close()
        pdf.image(chart_path, x=10, y=35, w=100); pdf.ln(50)
        if os.path.exists(chart_path): os.remove(chart_path)

        for page in report_data['pages']:
            pdf.set_font("Helvetica", "B", 14); pdf.set_fill_color(*hlcc_blue); pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 10, f"  PAGE: {page['page_name'].upper()}", ln=True, fill=True)
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 10); pdf.set_text_color(*hlcc_grey); pdf.set_fill_color(240, 240, 240)
            col_w = 47.5
            pdf.cell(col_w, 8, "NUM CHAT", 1, 0, 'C', fill=True)
            pdf.cell(col_w, 8, "BOOKING", 1, 0, 'C', fill=True)
            pdf.cell(col_w, 8, "VISIT", 1, 0, 'C', fill=True)
            pdf.cell(col_w, 8, "CLOSE DEAL", 1, 1, 'C', fill=True)
            pdf.set_font("Helvetica", "", 11); pdf.set_text_color(0, 0, 0)
            pdf.cell(col_w, 8, str(page['num_chat']), 1, 0, 'C')
            pdf.cell(col_w, 8, str(page['online_booking']), 1, 0, 'C')
            pdf.cell(col_w, 8, str(page['visit']), 1, 0, 'C')
            pdf.cell(col_w, 8, str(page['close_deal']), 1, 1, 'C')
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 10); pdf.set_fill_color(240, 240, 240); pdf.set_text_color(*hlcc_grey)
            pdf.cell(63.3, 8, "PACKAGE COUNT", 1, 0, 'C', fill=True)
            pdf.cell(63.3, 8, "MONTHLY REVENUE" if is_monthly else "DAILY REVENUE", 1, 0, 'C', fill=True)
            pdf.cell(63.3, 8, "TARGET REVENUE", 1, 1, 'C', fill=True)
            pdf.set_font("Helvetica", "B", 11); pdf.set_text_color(*hlcc_blue)
            pdf.cell(63.3, 8, str(page['package_count']), 1, 0, 'C')
            rev = page['total_sale_monthly'] if is_monthly else page['total_sale_today']
            pdf.cell(63.3, 8, f"${rev:,.2f}", 1, 0, 'C')
            pdf.cell(63.3, 8, f"${page['target_amount']:,.2f}", 1, 1, 'C')
            pdf.ln(4)

        pdf.set_y(-20); pdf.set_font("Helvetica", "I", 8); pdf.set_text_color(150)
        pdf.cell(0, 10, f"HLCC System Report - Generated: {datetime.now(tz).strftime('%d/%m/%Y %H:%M')}", 0, 0, 'C')
        
        prefix = "HLCC_Monthly_Report" if is_monthly else "HLCC_Daily_Report"
        file_name = f"{prefix}_{report_data['search_key']}.pdf"
        temp_dir = tempfile.gettempdir(); file_path = os.path.join(temp_dir, file_name)
        pdf.output(file_path)
        send_document(target_chat_id, file_path, f"📊 <b>HLCC Dashboard</b>\n📅 Date: {report_data['display_date']}")
        if os.path.exists(file_path): os.remove(file_path)
    finally:
        if loading_msg_id: delete_message(target_chat_id, loading_msg_id)

# ==========================================
# Generate Text Report (Updated with Fixed Monospace)
# ==========================================
def generate_and_send_report(requested_date_str, target_chat_id, is_monthly=False, loading_msg_id=None):
    try:
        try: target_date = parser.parse(requested_date_str)
        except: return
        report_data, is_success = fetch_report_data(target_date, is_monthly)
        today_for_display, search_key = report_data['display_date'], report_data['search_key']
        cache_key = f"M_REPORT_v{CACHE_VERSION}_{search_key}" if is_monthly else f"D_REPORT_v{CACHE_VERSION}_{search_key}"
        
        keyboard = {"inline_keyboard": [
            [{"text": f"📥 Download {'Monthly' if is_monthly else 'Daily'} PDF", "callback_data": f"{'mpdf_' if is_monthly else 'pdf_'}{search_key}"}],
            [{"text": "📅 ប្រចាំថ្ងៃ (Daily Report)", "callback_data": "ask_specific_date"}],
            [{"text": "📊 ប្រចាំខែ (Monthly Report)", "callback_data": "ask_monthly_report"}]
        ]}

        if cache_key in report_cache:
            cached_msg = report_cache[cache_key]
            if cached_msg == "EMPTY":
                send_simple_message(target_chat_id, f"📭 មិនមានទិន្នន័យសម្រាប់ {today_for_display} ទេ។", keyboard)
            else:
                send_simple_message(target_chat_id, cached_msg, keyboard)
            return

        if not is_success or not report_data['has_data']:
            report_cache[cache_key] = "EMPTY"
            send_simple_message(target_chat_id, f"📭 មិនមានទិន្នន័យសម្រាប់ {today_for_display} ទេ។", keyboard)
            return
        
        report_type = "Monthly" if is_monthly else "Daily"
        message = f"<b>HLCC – {report_type} Performance Report</b>\n"
        for page in report_data['pages']:
            p_name = page['page_name'].replace(" Page", "")
            message += f"📅 Period: {today_for_display} | 🌐 Page: {p_name}\n\n"
            
            message += "📈 <b>Activity Summary</b>\n"
            message += f"• 💬 Total chats: <code>{page['num_chat']:<4}</code>\n"
            message += f"• 📅 Bookings:    <code>{page['online_booking']:<4}</code>\n"
            message += f"• 📍 Visits:      <code>{page['visit']:<4}</code>\n"
            message += f"• ✅ Closed deals: <code>{page['close_deal']:<4}</code>\n"
            
            sale_val = page['total_sale_monthly'] if is_monthly else page['total_sale_today']
            sale_label = "Monthly" if is_monthly else "Today’s"
            message += f"• 💰 {sale_label} sales: <code>${sale_val:,.2f}</code>\n\n"
            
            message += "🔄 <b>Conversion Rates</b>\n"
            message += f"📅 Booking: <code>{page['rate_booking']}%</code> | 📍 Visit: <code>{page['rate_visit']}%</code>\n"
            message += f"✅ Deal:    <code>{page['rate_close_deal']}%</code> | 📦 Package: <code>{page['rate_package']}%</code>\n\n"
            
            message += "🎯 <b>Monthly Target</b>\n"
            message += f"🏆 Goal:     <code>${page['target_amount']:,.2f}</code>\n"
            message += f"💵 Actual:   <code>${page['total_sale_monthly']:,.2f}</code>\n"
            message += f"📊 Achieved: <b><code>{page['rate_sale']}%</code></b>\n"
            message += "====================\n\n"
        
        message += "Thank you 😊"
        report_cache[cache_key] = message
        send_simple_message(target_chat_id, message, keyboard)
    finally:
        if loading_msg_id: delete_message(target_chat_id, loading_msg_id)

# ==========================================
# Webhook Route
# ==========================================
@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if not update: return jsonify({"status": "ok"})
    if "callback_query" in update:
        cb = update["callback_query"]
        chat_id, data = cb["message"]["chat"]["id"], cb["data"]
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"]})
        
        if data == 'ask_monthly_report':
            year = datetime.now(tz).year
            months = ["មករា", "កុម្ភៈ", "មីនា", "មេសា", "ឧសភា", "មិថុនា", "កក្កដា", "សីហា", "កញ្ញា", "តុលា", "វិច្ឆិកា", "ធ្នូ"]
            rows, curr = [], []
            for i in range(12):
                curr.append({"text": months[i], "callback_data": f"mreport_{year}-{i+1:02d}"})
                if len(curr) == 3 or i == 11: rows.append(curr); curr = []
            send_simple_message(chat_id, f"📊 សូមជ្រើសរើស <b>ខែ</b> ៖", {"inline_keyboard": rows})
            
        elif data.startswith('mreport_'):
            sel_month = data.replace('mreport_', '')
            resp = send_simple_message(chat_id, f"⏳ កំពុងគណនាទិន្នន័យសរុបប្រចាំខែ <b>{sel_month}</b> ...")
            loading_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
            threading.Thread(target=generate_and_send_report, args=(sel_month, chat_id, True, loading_id)).start()

        elif data.startswith('mpdf_'):
            sel_month = data.replace('mpdf_', '')
            resp = send_simple_message(chat_id, f"📥 កំពុងបង្កើត Monthly PDF Dashboard សម្រាប់ខែ <b>{sel_month}</b> ...")
            loading_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
            threading.Thread(target=generate_and_send_pdf, args=(sel_month, chat_id, True, loading_id)).start()

        elif data == 'ask_specific_date' or data == 'back_to_months':
            year = datetime.now(tz).year
            months = ["មករា (Jan)", "កុម្ភៈ (Feb)", "មីនា (Mar)", "មេសា (Apr)", "ឧសភា (May)", "មិថុនា (Jun)", "កក្កដា (Jul)", "សីហា (Aug)", "កញ្ញា (Sep)", "តុលា (Oct)", "វិច្ឆិកា (Nov)", "ធ្នូ (Dec)"]
            rows, curr = [], []
            for i in range(12):
                curr.append({"text": months[i], "callback_data": f"month_{year}-{i+1:02d}"})
                if len(curr) == 3 or i == 11: rows.append(curr); curr = []
            send_simple_message(chat_id, f"📅 សូមជ្រើសរើស <b>ខែ</b> ៖", {"inline_keyboard": rows})

        elif data.startswith('month_'):
            sel_month = data.replace('month_', '')
            y, m = map(int, sel_month.split('-'))
            days = calendar.monthrange(y, m)[1]
            rows, curr = [], []
            for i in range(1, days + 1):
                curr.append({"text": str(i), "callback_data": f"report_{sel_month}-{i:02d}"})
                if len(curr) == 5 or i == days: rows.append(curr); curr = []
            rows.append([{"text": "⬅️ ត្រឡប់ក្រោយ (Back)", "callback_data": "back_to_months"}])
            send_simple_message(chat_id, f"📅 សូមជ្រើសរើស <b>ថ្ងៃទី</b> សម្រាប់ខែ {sel_month} ៖", {"inline_keyboard": rows})

        elif data.startswith('report_'):
            sel_date = data.replace('report_', '')
            resp = send_simple_message(chat_id, f"⏳ កំពុងស្វែងរកទិន្នន័យសម្រាប់ថ្ងៃ <b>{sel_date}</b> ...")
            loading_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
            threading.Thread(target=generate_and_send_report, args=(sel_date, chat_id, False, loading_id)).start()

        elif data.startswith('pdf_'):
            sel_date = data.replace('pdf_', '')
            resp = send_simple_message(chat_id, f"📥 កំពុងបង្កើតឯកសារ PDF សម្រាប់ថ្ងៃ <b>{sel_date}</b> ...")
            loading_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
            threading.Thread(target=generate_and_send_pdf, args=(sel_date, chat_id, False, loading_id)).start()

    return jsonify({"status": "ok"})

@app.route('/clear_cache', methods=['GET', 'POST'])
def clear_cache():
    global report_cache, CACHE_VERSION
    report_cache = {}; CACHE_VERSION += 1
    return jsonify({"status": "success"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
