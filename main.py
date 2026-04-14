import os
import re
import json
import threading
from datetime import datetime
import calendar
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request, jsonify, render_template
from dateutil import parser
import pytz
from fpdf import FPDF
import tempfile

# កំណត់ឲ្យ Flask ស្គាល់ folder 'templates'
app = Flask(__name__, template_folder='templates')

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'ដាក់_TOKEN_ទីនេះ')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', 'ដាក់_CHAT_ID_ទីនេះ')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', 'ដាក់_ID_Sheet_ទីនេះ')
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON', '')

TARGET_PAGES = ['Main Page', 'Sovanna']
NOTIFY_EMPTY_DATA = True
tz = pytz.timezone('Asia/Phnom_Penh')

report_cache = {}
CACHE_VERSION = 1

# ==========================================
# មុខងារភ្ជាប់ទៅកាន់ Telegram API 
# ==========================================
def telegram_api(method, payload, is_multipart=False):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        if is_multipart:
            requests.post(url, files=payload['files'], data=payload['data'], timeout=30)
        else:
            requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram API Error ({method}):", e)

def send_simple_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    telegram_api("sendMessage", payload)

def send_document(chat_id, file_path, caption):
    with open(file_path, 'rb') as f:
        payload = {
            'files': {'document': (os.path.basename(file_path), f, 'application/pdf')},
            'data': {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
        }
        telegram_api("sendDocument", payload, is_multipart=True)

# ==========================================
# មុខងារភ្ជាប់ Google Sheet API & ទាញទិន្នន័យ (រក្សាទុកកូដដដែល)
# ==========================================
def get_google_sheet():
    if not GOOGLE_CREDENTIALS_JSON: return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID)
    except:
        return None

def fetch_report_data(target_date):
    ss = get_google_sheet()
    if not ss: return None, False
    
    today_for_search = target_date.strftime("%Y-%m-%d")
    today_month_search = target_date.strftime("%Y-%m")
    today_for_display = target_date.strftime("%d/%m/%Y")
    
    target_y, target_m, target_d = target_date.year, target_date.month, target_date.day
    has_data = False
    pages_data = []

    for page_name in TARGET_PAGES:
        try:
            worksheet = ss.worksheet(page_name)
            data = worksheet.get_all_values()
        except gspread.exceptions.WorksheetNotFound:
            continue
        
        if len(data) <= 4: continue

        target_string = str(data[2][0]) if len(data[2]) > 0 else "0"
        match = re.search(r'\d+(\.\d+)?', target_string)
        target_amount = float(match.group(0)) if match else 0.0
        
        days_in_month = calendar.monthrange(target_date.year, target_date.month)[1]
        daily_target = target_amount / days_in_month if days_in_month else 0

        num_chat = online_booking = visit = close_deal = package_count = 0
        total_sale_amount = current_month_sale_amount = 0.0

        for i in range(4, len(data)):
            row = data[i]
            if len(row) < 2 or not row[1]: continue
            
            str_date = str(row[1]).strip()
            is_match_day = is_match_month = False

            if str_date.startswith(today_month_search): is_match_month = True
            
            if str_date == today_for_search:
                is_match_month = is_match_day = True
            elif not is_match_month:
                try:
                    p_date = parser.parse(str_date)
                    if p_date.year == target_y and p_date.month == target_m:
                        is_match_month = True
                        if p_date.day == target_d: is_match_day = True
                except: pass

            if is_match_month and len(row) > 7:
                try: current_month_sale_amount += float(row[7] or 0)
                except ValueError: pass

            if is_match_day:
                num_chat += 1
                if len(row) > 7:
                    try: total_sale_amount += float(row[7] or 0)
                    except ValueError: pass
                if len(row) > 9 and str(row[9]) in ['1', 'TRUE', 'true']: online_booking += 1
                if len(row) > 10 and str(row[10]) in ['1', 'TRUE', 'true']: visit += 1
                if len(row) > 11 and str(row[11]) in ['1', 'TRUE', 'true']: package_count += 1
                if len(row) > 12 and str(row[12]) in ['1', 'TRUE', 'true']: close_deal += 1

        if num_chat > 0 or total_sale_amount > 0:
            has_data = True
            rate_booking = f"{(online_booking / num_chat) * 100:.2f}" if num_chat > 0 else "0.00"
            rate_visit = f"{(visit / num_chat) * 100:.2f}" if num_chat > 0 else "0.00"
            rate_close_deal = f"{(close_deal / num_chat) * 100:.2f}" if num_chat > 0 else "0.00"
            rate_package = f"{(package_count / close_deal) * 100:.2f}" if close_deal > 0 else "0.00"
            rate_daily_amount = f"{(total_sale_amount / daily_target) * 100:.2f}" if daily_target > 0 else "0.00"
            rate_monthly_amount = f"{(current_month_sale_amount / target_amount) * 100:.2f}" if target_amount > 0 else "0.00"

            pages_data.append({
                "page_name": page_name,
                "num_chat": num_chat, "online_booking": online_booking, "visit": visit,
                "close_deal": close_deal, "package_count": package_count,
                "total_sale_amount": total_sale_amount, "current_month_sale_amount": current_month_sale_amount,
                "rate_booking": rate_booking, "rate_visit": rate_visit, "rate_close_deal": rate_close_deal,
                "rate_package": rate_package, "rate_daily_amount": rate_daily_amount, "rate_monthly_amount": rate_monthly_amount
            })

    return {
        "today_for_search": today_for_search,
        "today_for_display": today_for_display,
        "has_data": has_data,
        "pages": pages_data
    }, True

# ==========================================
# មុខងារបង្កើត និងបញ្ជូន PDF Dashboard
# ==========================================
def generate_and_send_pdf(requested_date_str, target_chat_id):
    try:
        target_date = parser.parse(requested_date_str)
    except:
        return

    report_data, is_success = fetch_report_data(target_date)
    if not is_success or not report_data['has_data']:
        send_simple_message(target_chat_id, "📭 មិនមានទិន្នន័យសម្រាប់ Export PDF ទេនាថ្ងៃនោះ។")
        return

    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    blue_color = (41, 128, 185)
    light_gray = (240, 240, 240)
    dark_text = (44, 62, 80)

    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*blue_color)
    pdf.cell(0, 10, "Marketing Sale Report Dashboard", ln=True, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, f"Date: {report_data['today_for_display']}", ln=True, align="C")
    pdf.ln(5)

    for page in report_data['pages']:
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_fill_color(*blue_color)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, f"  Page: {page['page_name']}", ln=True, fill=True)
        pdf.ln(3)

        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*dark_text)
        pdf.set_fill_color(*light_gray)
        col_w = 47.5  
        
        pdf.cell(col_w, 8, "Num Chat", 1, 0, 'C', fill=True)
        pdf.cell(col_w, 8, "Booking", 1, 0, 'C', fill=True)
        pdf.cell(col_w, 8, "Visit", 1, 0, 'C', fill=True)
        pdf.cell(col_w, 8, "Close Deal", 1, 1, 'C', fill=True)
        
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(col_w, 8, str(page['num_chat']), 1, 0, 'C')
        pdf.cell(col_w, 8, str(page['online_booking']), 1, 0, 'C')
        pdf.cell(col_w, 8, str(page['visit']), 1, 0, 'C')
        pdf.cell(col_w, 8, str(page['close_deal']), 1, 1, 'C')
        pdf.ln(2)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(63.3, 8, "Packages", 1, 0, 'C', fill=True)
        pdf.cell(63.3, 8, "Daily Sale ($)", 1, 0, 'C', fill=True)
        pdf.cell(63.3, 8, "Monthly Sale ($)", 1, 1, 'C', fill=True)

        pdf.set_font("Helvetica", "", 11)
        pdf.cell(63.3, 8, str(page['package_count']), 1, 0, 'C')
        pdf.cell(63.3, 8, f"${page['total_sale_amount']:.2f}", 1, 0, 'C')
        pdf.cell(63.3, 8, f"${page['current_month_sale_amount']:.2f}", 1, 1, 'C')
        pdf.ln(4)

        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*blue_color)
        pdf.cell(0, 8, "Conversion Rates (%)", 0, 1, 'L')
        
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*dark_text)
        rate_w = 31.6
        pdf.cell(rate_w, 7, "Booking %", 1, 0, 'C', fill=True)
        pdf.cell(rate_w, 7, "Visit %", 1, 0, 'C', fill=True)
        pdf.cell(rate_w, 7, "Deal %", 1, 0, 'C', fill=True)
        pdf.cell(rate_w, 7, "Package %", 1, 0, 'C', fill=True)
        pdf.cell(rate_w, 7, "Daily %", 1, 0, 'C', fill=True)
        pdf.cell(rate_w, 7, "Monthly %", 1, 1, 'C', fill=True)

        pdf.set_font("Helvetica", "", 10)
        pdf.cell(rate_w, 7, f"{page['rate_booking']}%", 1, 0, 'C')
        pdf.cell(rate_w, 7, f"{page['rate_visit']}%", 1, 0, 'C')
        pdf.cell(rate_w, 7, f"{page['rate_close_deal']}%", 1, 0, 'C')
        pdf.cell(rate_w, 7, f"{page['rate_package']}%", 1, 0, 'C')
        pdf.cell(rate_w, 7, f"{page['rate_daily_amount']}%", 1, 0, 'C')
        pdf.cell(rate_w, 7, f"{page['rate_monthly_amount']}%", 1, 1, 'C')
        pdf.ln(10) 

    pdf.set_y(-20)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(128)
    pdf.cell(0, 10, f"Generated automatically by System - Date: {datetime.now(tz).strftime('%d/%m/%Y %H:%M')}", 0, 0, 'C')

    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, f"Sale_Report_{report_data['today_for_search']}.pdf")
    pdf.output(file_path)

    send_document(target_chat_id, file_path, f"📊 <b>Dashboard Report (PDF)</b>\n📅 ថ្ងៃទី: {report_data['today_for_display']}\n\n<i>ទាញយកឯកសារនេះដើម្បីមើលទិន្នន័យបានច្បាស់ល្អ។</i>")
    
    if os.path.exists(file_path):
        os.remove(file_path)


# ==========================================
# មុខងារបញ្ជូនសារ Text 
# ==========================================
def generate_and_send_report(requested_date_str, target_chat_id):
    global report_cache
    try:
        target_date = parser.parse(requested_date_str)
    except:
        return

    today_for_search = target_date.strftime("%Y-%m-%d")
    today_for_display = target_date.strftime("%d/%m/%Y")
    cache_key = f"REPORT_v{CACHE_VERSION}_{today_for_search}"

    # ប៊ូតុងថ្មីសម្រាប់ផ្ញើត្រឡប់ទៅវិញ បន្ទាប់ពីទាញ Report ចេញពី Mini App 
    RENDER_WEB_APP_URL = f"https://report-oto.onrender.com/webapp?chat_id={target_chat_id}"
    keyboard = {"inline_keyboard": [[{"text": "📱 បើកផ្ទាំង Dashboard (Mini App)", "web_app": {"url": RENDER_WEB_APP_URL}}]]}

    if cache_key in report_cache:
        cached_msg = report_cache[cache_key]
        if cached_msg == "EMPTY":
            send_simple_message(target_chat_id, f"📭 មិនមានទិន្នន័យសម្រាប់ថ្ងៃ <b>{today_for_display}</b> នេះទេ។")
            return
        send_simple_message(target_chat_id, cached_msg, keyboard)
        return

    report_data, is_success = fetch_report_data(target_date)
    if not is_success or not report_data['has_data']:
        report_cache[cache_key] = "EMPTY"
        send_simple_message(target_chat_id, f"📭 មិនមានទិន្នន័យសម្រាប់ថ្ងៃ <b>{today_for_display}</b> នេះទេ។")
        return

    message = f"Dear Management\n💻 Marketing Sale Report\n🗓️ Date (<b>{today_for_display}</b>)\n\n"
    for page in report_data['pages']:
        message += f"🌐 Page: <b>{page['page_name']}</b>\n"
        message += f"» Number of Chat: <b>{page['num_chat']}</b>\n"
        message += f"» Online Booking: <b>{page['online_booking']}</b>\n"
        message += f"» Visit: <b>{page['visit']}</b>\n"
        message += f"» Close Deal: <b>{page['close_deal']}</b>\n"
        message += f"» Package: <b>{page['package_count']}</b>\n"
        message += f"» Total Sale Amount (Today): <b>${page['total_sale_amount']:.2f}</b>\n"
        message += f"» Total Sale Amount (Monthly): <b>${page['current_month_sale_amount']:.2f}</b>\n\n"
        message += "♻️ Summary Conversion rate %\n"
        message += f"» Online Booking: <b>{page['rate_booking']}%</b>\n"
        message += f"» Visit: <b>{page['rate_visit']}%</b>\n"
        message += f"» Close Deal: <b>{page['rate_close_deal']}%</b>\n"
        message += f"» Package: <b>{page['rate_package']}%</b>\n"
        message += f"» Daily Sale Amount: <b>{page['rate_daily_amount']}%</b>\n"
        message += f"» Monthly Sale Amount: <b>{page['rate_monthly_amount']}%</b>\n\n"
        message += "========================\n\n"
        
    message += "Thank you 😊"
    report_cache[cache_key] = message
    send_simple_message(target_chat_id, message, keyboard)


# ==========================================
# Flask Routes សម្រាប់ Web App និង API
# ==========================================

# ១. បង្ហាញផ្ទាំង UI នៅពេលចុចប៊ូតុងក្នុង Telegram
@app.route('/webapp')
def webapp():
    # Render នឹងទាញយកឯកសារ HTML ដែលយើងបានបង្កើតមកបង្ហាញ
    return render_template('index.html')

# ២. ទទួលការបញ្ជាពីផ្ទាំង UI
@app.route('/api/trigger', methods=['POST'])
def api_trigger():
    data = request.json
    target_date = data.get('date')
    report_type = data.get('type')  # 'text' ឬ 'pdf'
    chat_id = data.get('chat_id')

    if not target_date or not chat_id:
        return jsonify({"status": "error", "message": "Missing parameters"}), 400

    # ផ្ញើសារប្រាប់ជាមុនថា "កំពុងដំណើរការ..."
    file_type_text = "ឯកសារ PDF" if report_type == 'pdf' else "របាយការណ៍អត្ថបទ"
    send_simple_message(chat_id, f"⏳ កំពុងដំណើរការ <b>{file_type_text}</b> សម្រាប់ថ្ងៃ <b>{target_date}</b> ...")

    # ដំណើរការទាញយកក្នុង Background (Asynchronous)
    if report_type == 'pdf':
        threading.Thread(target=generate_and_send_pdf, args=(target_date, chat_id)).start()
    else:
        threading.Thread(target=generate_and_send_report, args=(target_date, chat_id)).start()

    return jsonify({"status": "ok", "message": "Triggered"})


@app.route('/clear_cache', methods=['GET', 'POST'])
def clear_cache():
    global report_cache, CACHE_VERSION
    report_cache = {}
    CACHE_VERSION += 1
    return jsonify({"status": "success", "message": f"Cache cleared."})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
