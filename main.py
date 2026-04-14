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

def send_document(chat_id, file_path, caption, thumb_path=None):
    f_doc = open(file_path, 'rb')
    files = {'document': (os.path.basename(file_path), f_doc, 'application/pdf')}
    f_thumb = None
    if thumb_path and os.path.exists(thumb_path):
        f_thumb = open(thumb_path, 'rb')
        files['thumbnail'] = (os.path.basename(thumb_path), f_thumb, 'image/png')
        
    payload = {'files': files, 'data': {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}}
    try: return telegram_api("sendDocument", payload, is_multipart=True)
    finally:
        f_doc.close()
        if f_thumb: f_thumb.close()

# ==========================================
# 100% Accurate Data Processing
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

def is_true(val):
    v = str(val).strip().lower()
    return v in ['1', 'true', 'checked', 'x', 'yes']

def fetch_report_data(target_date, is_monthly=False):
    ss = get_google_sheet()
    if not ss: return None, False
    today_month_search = target_date.strftime("%Y-%m")
    today_for_search = target_date.strftime("%Y-%m-%d")
    target_y, target_m, target_d = target_date.year, target_date.month, target_date.day
    has_data = False
    pages_data = []

    try: all_worksheets = {ws.title.strip().lower(): ws for ws in ss.worksheets()}
    except: all_worksheets = {}

    for page_name in TARGET_PAGES:
        worksheet = all_worksheets.get(page_name.strip().lower())
        if not worksheet: continue
        try: data = worksheet.get_all_values()
        except: continue
        if len(data) <= 4: continue
        
        target_amount = clean_currency(data[2][0] if len(data[2]) > 0 else "0")
        
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
                if len(row) > 9 and is_true(row[9]): online_booking += 1
                if len(row) > 10 and is_true(row[10]): visit += 1
                if len(row) > 11 and is_true(row[11]): package_count += 1
                if len(row) > 12 and is_true(row[12]): close_deal += 1
        
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
            
    return {"display_date": target_date.strftime("%B %d, %Y") if not is_monthly else target_date.strftime("%B %Y"), 
            "search_key": today_month_search if is_monthly else today_for_search,
            "has_data": has_data, "pages": pages_data}, True

# ==========================================
# Premium Style PDF Design
# ==========================================
def generate_and_send_pdf(requested_date_str, target_chat_id, is_monthly=False, loading_msg_id=None):
    try:
        try: target_date = parser.parse(requested_date_str)
        except: return
        report_data, is_success = fetch_report_data(target_date, is_monthly)
        
        keyboard = {"inline_keyboard": [
            [{"text": "📅 Daily Report", "callback_data": "ask_specific_date"}, {"text": "📊 Monthly Report", "callback_data": "ask_monthly_report"}],
            [{"text": "💬 Contact Developer", "url": "https://t.me/OUDOM333"}]
        ]}

        if not is_success or not report_data['has_data']:
            send_simple_message(target_chat_id, f"📭 No data available for {report_data['display_date']}.", keyboard)
            return
        
        pdf = FPDF(orientation='P', unit='mm', format='A4')
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        
        # --- Premium Color Palette ---
        PRIMARY_BLUE = (30, 64, 175)    # Elegant Deep Blue
        ACCENT_BLUE = (59, 130, 246)    # Soft Blue
        TEXT_MAIN = (55, 65, 81)        # Dark Gray
        TEXT_MUTED = (107, 114, 128)    # Light Gray
        BG_LIGHT = (249, 250, 251)      # Off-white
        
        logo_path = 'logo.png'
        bg_path = 'BG.png'

        # 1. Full Page Soft Background (BG.png)
        if os.path.exists(bg_path):
            with pdf.local_context(fill_opacity=0.05): # Very soft opacity
                pdf.image(bg_path, x=0, y=0, w=210, h=297)
        
        # 2. Executive Header
        if os.path.exists(logo_path):
            pdf.image(logo_path, x=85, y=10, w=40)
            pdf.set_y(50)
        else: pdf.set_y(20)

        pdf.set_font("Helvetica", "B", 18); pdf.set_text_color(*PRIMARY_BLUE)
        pdf.cell(0, 8, "HLCC INNOVATIVE BEAUTY CENTER", ln=True, align="C")
        pdf.set_font("Helvetica", "", 11); pdf.set_text_color(*TEXT_MUTED)
        title_type = "MONTHLY EXECUTIVE SUMMARY" if is_monthly else "DAILY PERFORMANCE REPORT"
        
        # FIXED: Removed the unsupported Unicode bullet point '•', replaced with '|'
        pdf.cell(0, 6, f"{title_type}  |  {report_data['display_date'].upper()}", ln=True, align="C")
        
        # Elegant Divider
        pdf.set_draw_color(229, 231, 235); pdf.set_line_width(0.5)
        pdf.line(20, pdf.get_y() + 4, 190, pdf.get_y() + 4); pdf.ln(10)

        # 3. Luxury Cards Layout
        for page in report_data['pages']:
            if pdf.get_y() > 240: pdf.add_page()
            x_start, y_start = 15, pdf.get_y()
            
            # Draw Card Background
            pdf.set_fill_color(255, 255, 255); pdf.set_draw_color(229, 231, 235)
            pdf.rect(x_start, y_start, 180, 52, 'DF')
            
            # Left Accent Bar
            pdf.set_fill_color(*PRIMARY_BLUE)
            pdf.rect(x_start, y_start, 3, 52, 'F')
            
            # Card Title
            pdf.set_xy(x_start + 8, y_start + 6)
            pdf.set_font("Helvetica", "B", 12); pdf.set_text_color(*PRIMARY_BLUE)
            pdf.cell(100, 6, page['page_name'].upper(), ln=False)
            
            # Total Revenue Top Right inside card
            pdf.set_font("Helvetica", "B", 12); pdf.set_text_color(*TEXT_MAIN)
            rev_val = page['total_sale_monthly'] if is_monthly else page['total_sale_today']
            pdf.cell(65, 6, f"${rev_val:,.2f}", align="R", ln=True)

            pdf.set_xy(x_start + 8, pdf.get_y())
            pdf.set_font("Helvetica", "", 8); pdf.set_text_color(*TEXT_MUTED)
            rev_label = "Monthly Revenue" if is_monthly else "Today's Revenue"
            pdf.cell(100, 4, f"Target: ${page['target_amount']:,.2f}", ln=False)
            pdf.cell(65, 4, rev_label, align="R", ln=True)
            
            pdf.ln(3)

            # Minimalist Metrics Row
            pdf.set_x(x_start + 8)
            col_w = 40
            metrics = [
                ("Total Chats", page['num_chat']),
                ("Bookings", page['online_booking']),
                ("Visits", page['visit']),
                ("Closed Deals", page['close_deal'])
            ]
            
            pdf.set_font("Helvetica", "", 8); pdf.set_text_color(*TEXT_MUTED)
            y_met_label = pdf.get_y()
            for i, (label, _) in enumerate(metrics):
                pdf.set_xy(x_start + 8 + (i * col_w), y_met_label)
                pdf.cell(col_w, 5, label, align="L")
            
            pdf.ln(5); pdf.set_font("Helvetica", "B", 14); pdf.set_text_color(*TEXT_MAIN)
            y_met_val = pdf.get_y()
            for i, (_, val) in enumerate(metrics):
                pdf.set_xy(x_start + 8 + (i * col_w), y_met_val)
                pdf.cell(col_w, 6, str(val), align="L")
            
            # Sleek Progress Bar
            pdf.set_y(pdf.get_y() + 10)
            bar_x, bar_y = x_start + 8, pdf.get_y()
            
            # Background Bar
            pdf.set_fill_color(243, 244, 246); pdf.rect(bar_x, bar_y, 140, 3, 'F')
            
            # Foreground Bar
            achieved = float(page['rate_sale'])
            fill_w = (min(achieved, 100) / 100) * 140
            pdf.set_fill_color(*ACCENT_BLUE); pdf.rect(bar_x, bar_y, fill_w, 3, 'F')
            
            # Percentage
            pdf.set_xy(bar_x + 145, bar_y - 1.5)
            pdf.set_font("Helvetica", "B", 9); pdf.set_text_color(*PRIMARY_BLUE)
            pdf.cell(20, 6, f"{page['rate_sale']}%", align="R", ln=True)
            
            pdf.ln(8)

        # 4. Premium Modern Chart
        if pdf.get_y() > 200: pdf.add_page()
        pdf.set_font("Helvetica", "B", 14); pdf.set_text_color(*PRIMARY_BLUE)
        pdf.ln(5); pdf.cell(0, 10, "ACHIEVEMENT OVERVIEW", ln=True, align="C")
        
        achieved_pct = [float(p['rate_sale']) for p in report_data['pages']]
        page_names = [p['page_name'].replace(" Page", "") for p in report_data['pages']]
        
        plt.figure(figsize=(9, 4))
        ax = plt.gca()
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#E5E7EB')
        ax.spines['bottom'].set_color('#E5E7EB')
        
        bars = plt.bar(page_names, achieved_pct, color='#3B82F6', alpha=0.85, width=0.55)
        plt.ylabel('Target Achieved (%)', fontsize=9, color='#6B7280')
        plt.xticks(fontsize=8, color='#374151', rotation=0)
        plt.yticks(fontsize=8, color='#6B7280')
        plt.ylim(0, max(max(achieved_pct) + 20, 110))
        plt.grid(axis='y', linestyle='-', alpha=0.4, color='#E5E7EB')
        
        for bar in bars:
            height = bar.get_height()
            plt.text(bar.get_x() + bar.get_width()/2, height + 2, f'{height}%', 
                     ha='center', va='bottom', fontsize=8, fontweight='bold', color='#1E40AF')
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
            plt.savefig(tmp.name, format='png', transparent=True, dpi=200, bbox_inches='tight')
            chart_path = tmp.name
        plt.close()
        
        pdf.image(chart_path, x=20, y=pdf.get_y(), w=170)
        os.remove(chart_path)
        pdf.set_y(pdf.get_y() + 65) 

        # 5. Minimalist QR & Footer
        qr = qrcode.QRCode(version=1, border=1, box_size=10)
        qr.add_data("https://t.me/OUDOM333"); qr.make(fit=True)
        img_qr = qr.make_image(fill_color="#1F2937", back_color="white").convert('RGB')
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as t_qr:
            img_qr.save(t_qr.name); q_p = t_qr.name
        
        pdf.set_fill_color(255, 255, 255)
        pdf.rect(179, 259, 18, 18, 'F')
        pdf.image(q_p, x=180, y=260, w=16); os.remove(q_p)
        
        pdf.set_y(263); pdf.set_x(120); pdf.set_font("Helvetica", "B", 8); pdf.set_text_color(*PRIMARY_BLUE)
        pdf.cell(57, 5, "CONTACT DEVELOPER", ln=True, align="R")
        pdf.set_x(120); pdf.set_font("Helvetica", "", 7); pdf.set_text_color(*TEXT_MUTED)
        pdf.cell(57, 4, "@OUDOM333", ln=True, align="R")

        pdf.set_y(-15); pdf.set_font("Helvetica", "", 8); pdf.set_text_color(156, 163, 175)
        
        # FIXED: Removed the unsupported Unicode bullet point '•', replaced with '|'
        pdf.cell(0, 10, f"Powered by OTO Messages  |  Generated on {datetime.now(tz).strftime('%d %b %Y, %H:%M')}", 0, 0, 'C')
        
        f_n = f"HLCC_Report_{report_data['search_key']}.pdf"; f_p = os.path.join(tempfile.gettempdir(), f_n); pdf.output(f_p)
        send_document(target_chat_id, f_p, f"💎 <b>HLCC {title_type}</b>\n📅 {report_data['display_date']}", thumb_path=logo_path)
        os.remove(f_p)
    finally:
        if loading_msg_id: delete_message(target_chat_id, loading_msg_id)

@app.route('/api/trigger', methods=['POST'])
def trigger_api():
    data = request.get_json(); req_date = data.get('date'); chat_id = data.get('chat_id')
    resp = send_simple_message(chat_id, f"⏳ Generating Executive PDF for <b>{req_date}</b> ...")
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
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        year = datetime.now(tz).year; rows, curr = [], []
        for i in range(12):
            curr.append({"text": months[i], "callback_data": f"mreport_{year}-{i+1:02d}"})
            if len(curr) == 3 or i == 11: rows.append(curr); curr = []
        send_simple_message(chat_id, f"📊 Select Month for Executive Report:", {"inline_keyboard": rows})
    elif data.startswith('mreport_'):
        delete_message(chat_id, current_msg_id); sel_month = data.replace('mreport_', '')
        resp = send_simple_message(chat_id, f"⏳ Generating Monthly PDF for <b>{sel_month}</b> ...")
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
        resp = send_simple_message(chat_id, f"⏳ Generating Daily PDF for <b>{sel_date}</b> ...")
        l_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
        threading.Thread(target=generate_and_send_pdf, args=(sel_date, chat_id, False, l_id)).start()
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
