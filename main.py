import os
import re
import json
import threading
from datetime import datetime
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
def telegram_api(method, payload, is_multipart=False, timeout=60):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    if TELEGRAM_TOPIC_ID:
        if is_multipart: payload['data']['message_thread_id'] = TELEGRAM_TOPIC_ID
        else: payload['message_thread_id'] = TELEGRAM_TOPIC_ID
    try:
        if is_multipart: 
            return requests.post(url, files=payload['files'], data=payload['data'], timeout=timeout)
        else: 
            return requests.post(url, json=payload, timeout=timeout)
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
    try: 
        return telegram_api("sendDocument", payload, is_multipart=True, timeout=120)
    finally:
        f_doc.close()
        if f_thumb: 
            f_thumb.close()

# ==========================================
# Robust Data Processing
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
        match = re.search(r'[-+]?\d*\.?\d+', clean_val)
        return float(match.group(0)) if match else 0.0
    except: return 0.0

def parse_date_flexible(date_str):
    if not date_str: return None
    date_str = str(date_str).strip()
    try: return parser.parse(date_str)
    except:
        for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"]:
            try: return datetime.strptime(date_str, fmt)
            except: continue
    return None

def is_true(val):
    v = str(val).strip().lower()
    return v in ['1', 'true', 'checked', 'x', 'yes']

def fetch_report_data(target_date, is_monthly=False):
    ss = get_google_sheet()
    if not ss: return None, False
    today_month_search = target_date.strftime("%Y-%m")
    today_for_search = target_date.strftime("%Y-%m-%d")
    target_y, target_m, target_d = target_date.year, target_date.month, target_date.day
    pages_data = []

    try: all_worksheets = {ws.title.strip().lower(): ws for ws in ss.worksheets()}
    except: all_worksheets = {}

    for page_name in TARGET_PAGES:
        worksheet = all_worksheets.get(page_name.strip().lower())
        num_chat = online_booking = visit = close_deal = package_count = 0
        total_sale_today = total_sale_monthly = target_amount = 0.0
        
        if worksheet:
            try:
                data = worksheet.get_all_values()
                if len(data) > 4:
                    target_str = str(data[2][0]) if len(data[2]) > 0 else "0"
                    target_amount = clean_currency(target_str)
                    for i in range(4, len(data)):
                        row = data[i]
                        if len(row) < 2 or not row[1]: continue
                        str_date = str(row[1]).strip()
                        is_match_day = is_match_month = False
                        p_date = parse_date_flexible(str_date)
                        if p_date:
                            if p_date.year == target_y and p_date.month == target_m:
                                is_match_month = True
                                if p_date.day == target_d: is_match_day = True
                        else:
                            if today_month_search in str_date: is_match_month = True
                            if today_for_search in str_date: is_match_day = True
                        
                        row_val = clean_currency(row[7]) if len(row) > 7 else 0.0
                        if is_match_month: 
                            total_sale_monthly += row_val
                        
                        row_match = is_match_month if is_monthly else is_match_day
                        if row_match:
                            num_chat += 1
                            total_sale_today += row_val
                            if len(row) > 9 and is_true(row[9]): online_booking += 1
                            if len(row) > 10 and is_true(row[10]): visit += 1
                            if len(row) > 11 and is_true(row[11]): package_count += 1
                            if len(row) > 12 and is_true(row[12]): close_deal += 1
            except: pass

        pages_data.append({
            "page_name": page_name, "num_chat": num_chat, "online_booking": online_booking, 
            "visit": visit, "close_deal": close_deal, "package_count": package_count,
            "total_sale_today": total_sale_today, "total_sale_monthly": total_sale_monthly,
            "target_amount": target_amount, 
            "rate_booking": f"{(online_booking / num_chat) * 100:.2f}" if num_chat > 0 else "0.00",
            "rate_visit": f"{(visit / num_chat) * 100:.2f}" if num_chat > 0 else "0.00",
            "rate_close_deal": f"{(close_deal / num_chat) * 100:.2f}" if num_chat > 0 else "0.00",
            "rate_package": f"{(package_count / close_deal) * 100:.2f}" if close_deal > 0 else "0.00",
            "rate_sale": f"{(total_sale_monthly / target_amount) * 100:.2f}" if target_amount > 0 else "0.00"
        })
    
    return {"display_date": target_date.strftime("%B %d, %Y") if not is_monthly else target_date.strftime("%B %Y"), 
            "search_key": today_month_search if is_monthly else today_for_search,
            "has_data": True, "pages": pages_data}, True

# ==========================================
# Premium Style PDF Design
# ==========================================
def generate_and_send_pdf(requested_date_str, target_chat_id, is_monthly=False, loading_msg_id=None):
    try:
        try: target_date = parser.parse(requested_date_str)
        except: return
        report_data, is_success = fetch_report_data(target_date, is_monthly)
        if not is_success: return
        
        pdf_w, pdf_h = 210, 680
        pdf = FPDF(orientation='P', unit='mm', format=(pdf_w, pdf_h)) 
        pdf.set_auto_page_break(auto=False)
        pdf.add_page()
        
        PRIMARY_BLUE = (30, 64, 175)
        ACCENT_BLUE = (59, 130, 246)
        TEXT_MAIN = (55, 65, 81)
        TEXT_MUTED = (107, 114, 128)
        
        logo_path = 'logo.png'
        bg_path = 'BG.png'

        # 1. Background Rendering (Fixed for Mi Browser)
        if os.path.exists(bg_path):
            try:
                with Image.open(bg_path) as img:
                    img = img.convert("RGBA")
                    alpha = img.split()[3]
                    alpha = alpha.point(lambda p: p * 0.08) 
                    img.putalpha(alpha)
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_bg:
                        img.save(tmp_bg.name)
                        pdf.image(tmp_bg.name, x=0, y=0, w=pdf_w, h=pdf_h)
                        os.remove(tmp_bg.name)
            except: pass
        
        # 2. Executive Header
        if os.path.exists(logo_path):
            logo_w = 58 
            pdf.image(logo_path, x=(pdf_w - logo_w)/2, y=12, w=logo_w)
            pdf.set_y(70)
        else: 
            pdf.set_y(25)

        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(*PRIMARY_BLUE)
        pdf.cell(0, 8, "HLCC INNOVATIVE BEAUTY CENTER", ln=True, align="C")
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*TEXT_MUTED)
        title_type = "MONTHLY EXECUTIVE SUMMARY" if is_monthly else "DAILY PERFORMANCE REPORT"
        pdf.cell(0, 6, f"{title_type}  |  {report_data['display_date'].upper()}", ln=True, align="C")
        
        pdf.set_draw_color(229, 231, 235)
        pdf.set_line_width(0.5)
        pdf.line(20, pdf.get_y() + 4, 190, pdf.get_y() + 4)
        pdf.ln(12)

        # 3. Cards Layout
        for page in report_data['pages']:
            x_start, y_start = 15, pdf.get_y()
            pdf.set_fill_color(255, 255, 255)
            pdf.set_draw_color(229, 231, 235)
            pdf.rect(x_start, y_start, 180, 56, 'DF')
            
            pdf.set_fill_color(*PRIMARY_BLUE)
            pdf.rect(x_start, y_start, 3, 56, 'F')
            
            pdf.set_xy(x_start + 8, y_start + 5)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(*PRIMARY_BLUE)
            pdf.cell(90, 6, page['page_name'].upper(), ln=False)
            
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*TEXT_MAIN)
            rev_label = "Monthly Rev" if is_monthly else "Today's Rev"
            rev_val = page['total_sale_monthly'] if is_monthly else page['total_sale_today']
            pdf.cell(80, 6, f"{rev_label}: ${rev_val:,.2f}", align="R", ln=True)

            pdf.set_y(pdf.get_y() + 2)
            pdf.set_x(x_start + 8)
            col_w = 42
            metrics = [
                ("Total Chats", page['num_chat']), 
                ("Bookings", page['online_booking']), 
                ("Visits", page['visit']), 
                ("Closed Deals", page['close_deal'])
            ]
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*TEXT_MUTED)
            y_m_lbl = pdf.get_y()
            for label, _ in metrics:
                pdf.set_xy(pdf.get_x(), y_m_lbl)
                pdf.cell(col_w, 4, label, align="L")
            
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(*TEXT_MAIN)
            y_m_val = y_m_lbl + 4
            pdf.set_x(x_start + 8)
            for _, val in metrics:
                pdf.set_xy(pdf.get_x(), y_m_val)
                pdf.cell(col_w, 5, str(val), align="L")
            
            pdf.set_y(y_m_val + 8)
            pdf.set_x(x_start + 8)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*PRIMARY_BLUE)
            pdf.cell(85, 5, "CONVERSION RATES", ln=False)
            pdf.cell(85, 5, "TARGET STATUS (MONTH)", ln=True)
            
            pdf.set_xy(x_start + 8, pdf.get_y())
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*TEXT_MAIN)
            pdf.cell(85, 5, f"Booking: {page['rate_booking']}%  |  Visit: {page['rate_visit']}%", ln=False)
            pdf.cell(85, 5, f"Goal: ${page['target_amount']:,.2f}", ln=True)
            
            pdf.set_xy(x_start + 8, pdf.get_y())
            pdf.cell(85, 5, f"Deal: {page['rate_close_deal']}%  |  Pkg: {page['rate_package']}%", ln=False)
            pdf.cell(85, 5, f"Actual: ${page['total_sale_monthly']:,.2f}", ln=True)
            
            pdf.set_xy(x_start + 8, pdf.get_y())
            pdf.cell(85, 5, "", ln=False)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(28, 5, f"Achieved: {page['rate_sale']}%", ln=False)
            
            bar_x, bar_y = pdf.get_x() + 2, pdf.get_y() + 1.2
            pdf.set_fill_color(243, 244, 246)
            pdf.rect(bar_x, bar_y, 45, 2.5, 'F')
            achieved = float(page['rate_sale'])
            fill_w = (min(achieved, 100) / 100) * 45
            
            pdf.set_fill_color(*ACCENT_BLUE)
            # FIXED SYNTAX ERROR HERE
            if fill_w > 0: 
                pdf.rect(bar_x, bar_y, fill_w, 2.5, 'F')
                
            pdf.ln(10)

        # 4. Chart Section
        pdf.ln(8)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*PRIMARY_BLUE)
        pdf.cell(0, 10, "ACHIEVEMENT OVERVIEW (%)", ln=True, align="C")
        
        achieved_pct = [float(p['rate_sale']) for p in report_data['pages']]
        achieved_str = [p['rate_sale'] for p in report_data['pages']]
        page_names = [p['page_name'].replace(" Page", "") for p in report_data['pages']]
        
        plt.figure(figsize=(9, 4.2))
        ax = plt.gca()
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#E5E7EB')
        ax.spines['bottom'].set_color('#E5E7EB')
        
        bars = plt.bar(page_names, achieved_pct, color='#3B82F6', alpha=0.85, width=0.5)
        plt.axhline(y=100, color='#10B981', linestyle='--', alpha=0.6, linewidth=1.2)
        plt.text(-0.45, 102, 'Goal 100%', color='#10B981', fontsize=7, fontweight='bold')
        
        plt.ylabel('Target Achieved (%)', fontsize=9, color='#6B7280')
        plt.ylim(0, max(max(achieved_pct) + 25, 120))
        plt.grid(axis='y', linestyle='-', alpha=0.3, color='#E5E7EB')
        
        for bar, rate_str in zip(bars, achieved_str):
            h = bar.get_height()
            plt.text(bar.get_x() + bar.get_width()/2, h + 3, f'{rate_str}%', ha='center', va='bottom', fontsize=8, fontweight='bold', color='#1E40AF')
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_c:
            plt.savefig(tmp_c.name, format='png', transparent=True, dpi=200, bbox_inches='tight')
            pdf.image(tmp_c.name, x=20, y=pdf.get_y(), w=170)
            os.remove(tmp_c.name)
        plt.close()
        
        # 5. FOOTER AREA (QR next to Credit)
        footer_base_y = 660
        
        qr = qrcode.QRCode(version=1, border=1, box_size=10)
        qr.add_data("https://t.me/OUDOM333")
        qr.make(fit=True)
        img_qr = qr.make_image(fill_color="#1F2937", back_color="white").convert('RGB')
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as t_qr:
            img_qr.save(t_qr.name)
            qr_w = 16
            pdf.image(t_qr.name, x=175, y=footer_base_y - 6, w=qr_w)
            os.remove(t_qr.name)
        
        pdf.set_y(footer_base_y)
        pdf.set_x(20)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(156, 163, 175)
        pdf.cell(150, 4, f"Powered by OTO Messages  |  Generated on {datetime.now(tz).strftime('%d %b %Y, %H:%M')}", 0, 0, 'L')
        
        pdf.set_xy(165, footer_base_y + 10)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*PRIMARY_BLUE)
        pdf.cell(35, 4, "HELP & SUPPORT: @OUDOM333", 0, 0, 'R')
        
        f_n = f"HLCC_Premium_{report_data['search_key']}.pdf"
        f_p = os.path.join(tempfile.gettempdir(), f_n)
        pdf.output(f_p)
        send_document(target_chat_id, f_p, f"💎 <b>HLCC Executive Dashboard</b>\n📅 {report_data['display_date']}", thumb_path=logo_path)
        os.remove(f_p)
    finally:
        if loading_msg_id: 
            delete_message(target_chat_id, loading_msg_id)

@app.route('/api/trigger', methods=['POST'])
def trigger_api():
    data = request.get_json()
    req_date = data.get('date')
    chat_id = data.get('chat_id')
    resp = send_simple_message(chat_id, f"⏳ Generating Executive PDF for <b>{req_date}</b> ...")
    l_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
    threading.Thread(target=generate_and_send_pdf, args=(req_date, chat_id, False, l_id)).start()
    return jsonify({"status": "processing"})

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if not update: 
        return jsonify({"status": "ok"})
        
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        text = msg.get("text", "")
        chat_id = msg["chat"]["id"]
        
        if text.startswith("/start"):
            kb = {"inline_keyboard": [
                [{"text": "📅 Daily Report", "callback_data": "ask_specific_date"}, {"text": "📊 Monthly Report", "callback_data": "ask_monthly_report"}],
                [{"text": "💬 Help & Support", "url": "https://t.me/OUDOM333"}]
            ]}
            send_simple_message(chat_id, "👋 <b>Welcome to HLCC Reporting System!</b>", kb)
            return jsonify({"status": "ok"})
            
    if "callback_query" in update:
        cb = update["callback_query"]
        chat_id = cb["message"]["chat"]["id"]
        data = cb["data"]
        c_m_id = cb["message"]["message_id"]
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"]})
        
        if data == 'ask_monthly_report':
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            rows, curr = [], []
            year = datetime.now(tz).year
            for i in range(12):
                curr.append({"text": months[i], "callback_data": f"mreport_{year}-{i+1:02d}"})
                if len(curr) == 3 or i == 11: 
                    rows.append(curr)
                    curr = []
            send_simple_message(chat_id, "📊 Select Month:", {"inline_keyboard": rows})
            
        elif data.startswith('mreport_'):
            delete_message(chat_id, c_m_id)
            sel_month = data.replace('mreport_', '')
            resp = send_simple_message(chat_id, f"⏳ Generating Monthly PDF for <b>{sel_month}</b> ...")
            l_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
            threading.Thread(target=generate_and_send_pdf, args=(sel_month, chat_id, True, l_id)).start()
            
        elif data == 'ask_specific_date' or data == 'back_to_months':
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            rows, curr = [], []
            year = datetime.now(tz).year
            for i in range(12):
                curr.append({"text": months[i], "callback_data": f"month_{year}-{i+1:02d}"})
                if len(curr) == 3 or i == 11: 
                    rows.append(curr)
                    curr = []
            send_simple_message(chat_id, "📅 Select Month:", {"inline_keyboard": rows})
            
        elif data.startswith('month_'):
            delete_message(chat_id, c_m_id)
            sel_month = data.replace('month_', '')
            y, m = map(int, sel_month.split('-'))
            days = 31 
            rows, curr = [], []
            for i in range(1, days + 1):
                curr.append({"text": str(i), "callback_data": f"report_{sel_month}-{i:02d}"})
                if len(curr) == 5 or i == days: 
                    rows.append(curr)
                    curr = []
            rows.append([{"text": "⬅️ Back", "callback_data": "back_to_months"}])
            send_simple_message(chat_id, f"📅 Select Day for {sel_month}:", {"inline_keyboard": rows})
            
        elif data.startswith('report_'):
            delete_message(chat_id, c_m_id)
            sel_date = data.replace('report_', '')
            resp = send_simple_message(chat_id, f"⏳ Generating Daily PDF for <b>{sel_date}</b> ...")
            l_id = resp.json().get('result', {}).get('message_id') if resp and resp.status_code == 200 else None
            threading.Thread(target=generate_and_send_pdf, args=(sel_date, chat_id, False, l_id)).start()
            
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
