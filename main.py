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
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import qrcode
from PIL import Image

app = Flask(__name__)

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
TELEGRAM_TOPIC_ID = os.getenv('TELEGRAM_TOPIC_ID', '') 

SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '')
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON', '')

TARGET_PAGES = ['Main Page', 'Sovanna', 'Esthetic RX', 'Toul Kork', 'Mega Mall', 'Olympia', 'PHBS']
tz = pytz.timezone('Asia/Phnom_Penh')

report_cache = {}
CACHE_VERSION = 5

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
# Data Processing
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

def clean_currency(value):
    if not value: return 0.0
    try:
        clean_val = str(value).replace('$', '').replace(',', '').strip()
        return float(clean_val) if clean_val else 0.0
    except: return 0.0

def fetch_report_data(target_date, is_monthly=False):
    ss = get_google_sheet()
    if not ss: return None, False
    today_month_search = target_date.strftime("%Y-%m")
    today_for_search = target_date.strftime("%Y-%m-%d")
    target_y, target_m, target_d = target_date.year, target_date.month, target_date.day
    has_data = False
    pages_data = []

    try: all_worksheets = {ws.title.strip(): ws for ws in ss.worksheets()}
    except: all_worksheets = {}

    for page_name in TARGET_PAGES:
        worksheet = all_worksheets.get(page_name.strip())
        if not worksheet: continue
        try: data = worksheet.get_all_values()
        except: continue
        if len(data) <= 4: continue
        
        target_string = str(data[2][0]) if len(data[2]) > 0 else "0"
        match = re.search(r'\d+(\.\d+)?', target_string.replace(',', ''))
        target_amount = float(match.group(0)) if match else 0.0
        
        num_chat = online_booking = visit = close_deal = package_count = 0
        total_sale_today = total_sale_monthly = 0.0
        
        for i in range(4, len(data)):
            row = data[i]
            if len(row) < 2 or not row[1]: continue
            str_date = str(row[1]).strip()
            is_match_day = is_match_month = False
            try:
                p_date = parser.parse(str_date, fuzzy=False)
                if p_date.year == target_y and p_date.month == target_m:
                    is_match_month = True
                    if p_date.day == target_d: is_match_day = True
            except:
                if str_date.startswith(today_month_search): is_match_month = True
                if str_date == today_for_search: is_match_day = True

            row_val = clean_currency(row[7]) if len(row) > 7 else 0.0
            if is_match_month: total_sale_monthly += row_val
            
            row_match = is_match_month if is_monthly else is_match_day
            if row_match:
                num_chat += 1
                total_sale_today += row_val
                if len(row) > 9 and str(row[9]).lower() in ['1', 'true']: online_booking += 1
                if len(row) > 10 and str(row[10]).lower() in ['1', 'true']: visit += 1
                if len(row) > 11 and str(row[11]).lower() in ['1', 'true']: package_count += 1
                if len(row) > 12 and str(row[12]).lower() in ['1', 'true']: close_deal += 1
        
        if num_chat > 0 or total_sale_monthly > 0:
            has_data = True
            pages_data.append({
                "page_name": page_name, "num_chat": num_chat, "online_booking": online_booking, 
                "visit": visit, "close_deal": close_deal, "package_count": package_count,
                "total_sale_today": total_sale_today, "total_sale_monthly": total_sale_monthly,
                "target_amount": target_amount, 
                "rate_booking": f"{(online_booking / num_chat) * 100:.1f}" if num_chat > 0 else "0.0",
                "rate_visit": f"{(visit / num_chat) * 100:.1f}" if num_chat > 0 else "0.0",
                "rate_close_deal": f"{(close_deal / num_chat) * 100:.1f}" if num_chat > 0 else "0.0",
                "rate_sale": f"{(total_sale_monthly / target_amount) * 100:.1f}" if target_amount > 0 else "0.0"
            })
            
    return {"display_date": target_date.strftime("%B %Y") if is_monthly else target_date.strftime("%d/%m/%Y"), 
            "search_key": today_month_search if is_monthly else today_for_search,
            "has_data": has_data, "pages": pages_data}, True

# ==========================================
# Modern Luxury PDF Design
# ==========================================
def generate_and_send_pdf(requested_date_str, target_chat_id, is_monthly=False, loading_msg_id=None):
    try:
        try: target_date = parser.parse(requested_date_str)
        except: return
        report_data, is_success = fetch_report_data(target_date, is_monthly)
        
        keyboard = {"inline_keyboard": [
            [{"text": "📅 Daily", "callback_data": "ask_specific_date"}, {"text": "📊 Monthly", "callback_data": "ask_monthly_report"}],
            [{"text": "💬 Help & Support", "url": "https://t.me/OUDOM333"}]
        ]}

        if not is_success or not report_data['has_data']:
            send_simple_message(target_chat_id, f"📭 No data available for {report_data['display_date']}.", keyboard)
            return
        
        pdf = FPDF(orientation='P', unit='mm', format='A4')
        pdf.add_page()
        
        # Style Tokens
        HLCC_BLUE = (52, 157, 216)
        HLCC_PINK = (255, 182, 193)
        TEXT_DARK = (40, 40, 40)
        TEXT_LIGHT = (120, 120, 120)
        CARD_BG = (255, 255, 255)
        
        logo_path = 'logo.png'
        bg_path = 'BG.png'

        # 1. Background Image (BG.png)
        if os.path.exists(bg_path):
            with pdf.local_context(fill_opacity=0.04):
                pdf.image(bg_path, x=0, y=0, w=210, h=297)
        
        # 2. Header
        if os.path.exists(logo_path):
            pdf.image(logo_path, x=85, y=12, w=40)
            pdf.set_y(55)
        else: pdf.set_y(25)

        pdf.set_font("Helvetica", "B", 20); pdf.set_text_color(*HLCC_BLUE)
        pdf.cell(0, 10, "HLCC INNOVATIVE BEAUTY CENTER", ln=True, align="C")
        pdf.set_font("Helvetica", "", 12); pdf.set_text_color(*TEXT_LIGHT)
        title_type = "MONTHLY PERFORMANCE" if is_monthly else "DAILY SALES REPORT"
        pdf.cell(0, 7, f"{title_type} | {report_data['display_date']}", ln=True, align="C")
        pdf.ln(10)

        # 3. Cards Layout
        for page in report_data['pages']:
            # Start Card
            pdf.set_fill_color(*CARD_BG); pdf.set_draw_color(230, 230, 230)
            x_start, y_start = 15, pdf.get_y()
            pdf.rect(x_start, y_start, 180, 58, 'FD') # Card body
            
            # Card Header (Accent line)
            pdf.set_fill_color(*HLCC_BLUE)
            pdf.rect(x_start, y_start, 180, 1.5, 'F')
            
            # Branch Name
            pdf.set_xy(x_start + 5, y_start + 5)
            pdf.set_font("Helvetica", "B", 14); pdf.set_text_color(*HLCC_BLUE)
            pdf.cell(0, 8, page['page_name'].upper(), ln=True)
            
            # Metrics Grid (4 columns)
            pdf.set_y(pdf.get_y() + 2)
            metric_w = 42
            metrics = [
                ("CHATS", page['num_chat']),
                ("BOOKINGS", page['online_booking']),
                ("VISITS", page['visit']),
                ("DEALS", page['close_deal'])
            ]
            
            pdf.set_font("Helvetica", "B", 8); pdf.set_text_color(*TEXT_LIGHT)
            start_y_metrics = pdf.get_y()
            for i, (label, _) in enumerate(metrics):
                pdf.set_xy(x_start + 5 + (i * metric_w), start_y_metrics)
                pdf.cell(metric_w, 5, label, align="L")
            
            pdf.ln(5); pdf.set_font("Helvetica", "B", 13); pdf.set_text_color(*TEXT_DARK)
            val_y = pdf.get_y()
            for i, (_, val) in enumerate(metrics):
                pdf.set_xy(x_start + 5 + (i * metric_w), val_y)
                pdf.cell(metric_w, 7, str(val), align="L")
            
            # Revenue & Progress Bar Section
            pdf.set_y(pdf.get_y() + 10)
            pdf.set_font("Helvetica", "B", 9); pdf.set_text_color(*TEXT_LIGHT)
            pdf.set_x(x_start + 5)
            pdf.cell(40, 5, "MONTHLY ACHIEVEMENT", align="L")
            
            # Progress Bar Background
            bar_x, bar_y = x_start + 5, pdf.get_y() + 6
            pdf.set_fill_color(245, 245, 245); pdf.rect(bar_x, bar_y, 110, 4, 'F')
            
            # Progress Bar Foreground
            achieved = float(page['rate_sale'])
            fill_w = (min(achieved, 100) / 100) * 110
            pdf.set_fill_color(*HLCC_BLUE); pdf.rect(bar_x, bar_y, fill_w, 4, 'F')
            
            # Revenue Text
            pdf.set_xy(x_start + 120, bar_y - 1)
            pdf.set_font("Helvetica", "B", 11); pdf.set_text_color(*HLCC_BLUE)
            rev_val = page['total_sale_monthly'] if is_monthly else page['total_sale_today']
            pdf.cell(55, 5, f"${rev_val:,.2f} / ${page['target_amount']:,.0f}", align="R", ln=True)
            
            # Conversion Mini Labels
            pdf.set_font("Helvetica", "", 7); pdf.set_text_color(*TEXT_LIGHT)
            pdf.set_x(x_start + 5)
            pdf.cell(0, 5, f"Conversion: Booking {page['rate_booking']}% | Visit {page['rate_visit']}% | Deal {page['rate_close_deal']}% | Achievement {page['rate_sale']}%", ln=True)
            
            pdf.ln(12) # Space between cards

        # 4. Summary Chart at Bottom
        if pdf.get_y() > 200: pdf.add_page()
        pdf.set_font("Helvetica", "B", 12); pdf.set_text_color(*HLCC_BLUE)
        pdf.cell(0, 10, "PERFORMANCE OVERVIEW (%)", ln=True, align="C")
        
        achieved_pct = [float(p['rate_sale']) for p in report_data['pages']]
        page_names = [p['page_name'].replace(" Page", "") for p in report_data['pages']]
        
        plt.figure(figsize=(8, 3))
        plt.rcParams['axes.facecolor'] = 'none'
        bars = plt.bar(page_names, achieved_pct, color='#349dd8', alpha=0.8, width=0.6)
        plt.title('Sales Achievement vs Target', fontsize=10, color='#333333')
        plt.ylim(0, max(max(achieved_pct) + 20, 110))
        plt.grid(axis='y', linestyle='--', alpha=0.3)
        for bar in bars:
            plt.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2, f'{bar.get_height()}%', ha='center', va='bottom', fontsize=8, fontweight='bold')
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
            plt.savefig(tmp.name, format='png', transparent=True, dpi=120); c_p = tmp.name
        plt.close(); pdf.image(c_p, x=30, y=pdf.get_y(), w=150); os.remove(c_p)

        # 5. QR Contact & Footer
        qr = qrcode.QRCode(border=2); qr.add_data("https://t.me/OUDOM333"); qr.make(fit=True)
        img_qr = qr.make_image(fill_color="black", back_color="white").convert('RGB')
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as t_qr: img_qr.save(t_qr.name); q_p = t_qr.name
        pdf.set_fill_color(255, 255, 255); pdf.rect(174, 261, 22, 22, 'F'); pdf.image(q_p, x=175, y=262, w=20); os.remove(q_p)
        pdf.set_y(265); pdf.set_x(120); pdf.set_font("Helvetica", "B", 8); pdf.set_text_color(*HLCC_BLUE)
        pdf.cell(53, 5, "CONTACT DEVELOPER", ln=True, align="R")
        pdf.set_x(120); pdf.set_font("Helvetica", "", 7); pdf.set_text_color(100); pdf.cell(53, 4, "@OUDOM333", ln=True, align="R")
        pdf.set_y(-15); pdf.set_font("Helvetica", "I", 8); pdf.set_text_color(180)
        pdf.cell(0, 10, f"System by OTO Messages | HLCC Official Report | Gen: {datetime.now(tz).strftime('%d/%m/%Y %H:%M')}", 0, 0, 'C')
        
        f_n = f"HLCC_Report_{report_data['search_key']}.pdf"; f_p = os.path.join(tempfile.gettempdir(), f_n); pdf.output(f_p)
        send_document(target_chat_id, f_p, f"💎 <b>HLCC {title_type}</b>\n📅 Period: {report_data['display_date']}")
        send_simple_message(target_chat_id, "✅ Report generated successfully.", keyboard)
        os.remove(f_p)
    finally:
        if loading_msg_id: delete_message(target_chat_id, loading_msg_id)

@app.route('/api/trigger', methods=['POST'])
def trigger_api():
    data = request.get_json(); req_date = data.get('date'); chat_id = data.get('chat_id')
    resp = send_simple_message(chat_id, f"⏳ Generating Luxury PDF Dashboard for <b>{req_date}</b> ...")
    l_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
    threading.Thread(target=generate_and_send_pdf, args=(req_date, chat_id, False, l_id)).start()
    return jsonify({"status": "processing"})

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if not update or "callback_query" not in update: return jsonify({"status": "ok"})
    cb = update["callback_query"]; chat_id, data, current_msg_id = cb["message"]["chat"]["id"], cb["data"], cb["message"]["message_id"]
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"]})
    
    if data == 'ask_monthly_report':
        months = ["មករា (Jan)", "កុម្ភៈ (Feb)", "មីនា (Mar)", "មេសា (Apr)", "ឧសភា (May)", "មិថុនា (Jun)", "កក្កដា (Jul)", "សីហា (Aug)", "កញ្ញា (Sep)", "តុលា (Oct)", "វិច្ឆិកា (Nov)", "ធ្នូ (Dec)"]
        year = datetime.now(tz).year; rows, curr = [], []
        for i in range(12):
            curr.append({"text": months[i], "callback_data": f"mreport_{year}-{i+1:02d}"})
            if len(curr) == 3 or i == 11: rows.append(curr); curr = []
        send_simple_message(chat_id, f"📊 Please select **Month** for Dashboard:", {"inline_keyboard": rows})
    elif data.startswith('mreport_'):
        delete_message(chat_id, current_msg_id); sel_month = data.replace('mreport_', '')
        resp = send_simple_message(chat_id, f"⏳ Creating Monthly PDF for <b>{sel_month}</b> ...")
        l_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
        threading.Thread(target=generate_and_send_pdf, args=(sel_month, chat_id, True, l_id)).start()
    elif data == 'ask_specific_date' or data == 'back_to_months':
        year = datetime.now(tz).year; months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        rows, curr = [], []
        for i in range(12):
            curr.append({"text": months[i], "callback_data": f"month_{year}-{i+1:02d}"})
            if len(curr) == 3 or i == 11: rows.append(curr); curr = []
        send_simple_message(chat_id, f"📅 Select Month:", {"inline_keyboard": rows})
    elif data.startswith('month_'):
        delete_message(chat_id, current_msg_id); sel_month = data.replace('month_', '')
        y, m = map(int, sel_month.split('-')); days = calendar.monthrange(y, m)[1]; rows, curr = [], []
        for i in range(1, days + 1):
            curr.append({"text": str(i), "callback_data": f"report_{sel_month}-{i:02d}"})
            if len(curr) == 5 or i == days: rows.append(curr); curr = []
        rows.append([{"text": "⬅️ Back", "callback_data": "back_to_months"}])
        send_simple_message(chat_id, f"📅 Select Day for {sel_month}:", {"inline_keyboard": rows})
    elif data.startswith('report_'):
        delete_message(chat_id, current_msg_id); sel_date = data.replace('report_', '')
        resp = send_simple_message(chat_id, f"⏳ Creating PDF for <b>{sel_date}</b> ...")
        l_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
        threading.Thread(target=generate_and_send_pdf, args=(sel_date, chat_id, False, l_id)).start()
    return jsonify({"status": "ok"})

@app.route('/clear_cache', methods=['GET', 'POST'])
def clear_cache():
    global report_cache; report_cache = {}; return jsonify({"status": "success"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
