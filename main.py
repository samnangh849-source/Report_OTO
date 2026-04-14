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

app = Flask(__name__)

# ១. ទាញយកព័ត៌មានពី Environment Variables របស់ Render
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

def edit_message_text(chat_id, message_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    telegram_api("editMessageText", payload)

def answer_callback(callback_id):
    telegram_api("answerCallbackQuery", {"callback_query_id": callback_id})

def send_document(chat_id, file_path, caption):
    with open(file_path, 'rb') as f:
        payload = {
            'files': {'document': (os.path.basename(file_path), f, 'application/pdf')},
            'data': {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
        }
        telegram_api("sendDocument", payload, is_multipart=True)

# ==========================================
# មុខងារភ្ជាប់ទៅកាន់ Google Sheet API
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

# ==========================================
# មុខងារស្នូល៖ ទាញយកទិន្នន័យ (ប្រើរួមសម្រាប់ Text និង PDF)
# ==========================================
def fetch_report_data(target_date):
    ss = get_google_sheet()
    if not ss: return None, False
    
    today_for_search = target_date.strftime("%Y-%m-%d")
    today_month_search = target_date.strftime("%Y-%m")
    today_for_display = target_date.strftime("%d/%m/%Y")
    
    target_y = target_date.year
    target_m = target_date.month
    target_d = target_date.day
    
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
        clean_date_str = re.sub(r'[()]', '', requested_date_str).strip()
        target_date = parser.parse(clean_date_str)
    except:
        send_simple_message(target_chat_id, "❌ កាលបរិច្ឆេទមិនត្រឹមត្រូវ។")
        return

    # ទាញទិន្នន័យ
    report_data, is_success = fetch_report_data(target_date)
    if not is_success or not report_data['has_data']:
        send_simple_message(target_chat_id, "📭 មិនមានទិន្នន័យសម្រាប់ Export PDF ទេ។")
        return

    # ការរចនា PDF (Dashboard Design)
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # ពណ៌ Theme 
    blue_color = (41, 128, 185)
    light_gray = (240, 240, 240)
    dark_text = (44, 62, 80)

    # ផ្នែកក្បាល (Header)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*blue_color)
    pdf.cell(0, 10, "Marketing Sale Report Dashboard", ln=True, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, f"Date: {report_data['today_for_display']}", ln=True, align="C")
    pdf.ln(5)

    # គូរទិន្នន័យសម្រាប់ Page នីមួយៗ
    for page in report_data['pages']:
        # ចំណងជើង Page
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_fill_color(*blue_color)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, f"  Page: {page['page_name']}", ln=True, fill=True)
        pdf.ln(3)

        # តារាង Metrics ជួរទី១
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*dark_text)
        pdf.set_fill_color(*light_gray)
        col_w = 47.5  # បែងចែកជា 4 ជួរ (190mm / 4)
        
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

        # តារាង Financial & Package
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(63.3, 8, "Packages", 1, 0, 'C', fill=True)
        pdf.cell(63.3, 8, "Daily Sale ($)", 1, 0, 'C', fill=True)
        pdf.cell(63.3, 8, "Monthly Sale ($)", 1, 1, 'C', fill=True)

        pdf.set_font("Helvetica", "", 11)
        pdf.cell(63.3, 8, str(page['package_count']), 1, 0, 'C')
        pdf.cell(63.3, 8, f"${page['total_sale_amount']:.2f}", 1, 0, 'C')
        pdf.cell(63.3, 8, f"${page['current_month_sale_amount']:.2f}", 1, 1, 'C')
        pdf.ln(4)

        # តារាង Conversion Rates
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
        pdf.ln(10) # ឃ្លាតពី Page មួយទៅ Page មួយទៀត

    pdf.set_y(-20)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(128)
    pdf.cell(0, 10, f"Generated automatically by System • Date: {datetime.now(tz).strftime('%d/%m/%Y %H:%M')}", 0, 0, 'C')

    # រក្សាទុក PDF ទៅក្នុង /tmp របស់ Render រួចបញ្ជូនទៅ Telegram
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, f"Sale_Report_{report_data['today_for_search']}.pdf")
    pdf.output(file_path)

    # ផ្ញើ PDF ទៅ Telegram
    send_document(target_chat_id, file_path, f"📊 <b>Dashboard Report (PDF)</b>\n📅 ថ្ងៃទី: {report_data['today_for_display']}\n\n<i>ទាញយកឯកសារនេះដើម្បីមើលទិន្នន័យបានច្បាស់ល្អ។</i>")
    
    # លុបឯកសារចេញពី Render វិញដើម្បីកុំអោយពេញ Memory
    if os.path.exists(file_path):
        os.remove(file_path)


# ==========================================
# មុខងារបញ្ជូនសារ Text (ដូចដើម តែប្រើ Function ថ្មី)
# ==========================================
def generate_and_send_report(requested_date_str, target_chat_id):
    global report_cache
    
    try:
        if requested_date_str:
            clean_date_str = re.sub(r'[()]', '', requested_date_str).strip()
            target_date = parser.parse(clean_date_str)
        else:
            target_date = datetime.now(tz)
    except ValueError:
        send_simple_message(target_chat_id, f"❌ កាលបរិច្ឆេទ <b>'{requested_date_str}'</b> មិនត្រឹមត្រូវទេ។")
        return

    today_for_search = target_date.strftime("%Y-%m-%d")
    today_for_display = target_date.strftime("%d/%m/%Y")
    cache_key = f"REPORT_v{CACHE_VERSION}_{today_for_search}"
    
    # រៀបចំប៊ូតុង ដែលមានបញ្ចូល "Export PDF" បន្ថែម
    keyboard = {"inline_keyboard": [
        [{"text": "📥 ទាញយកជា PDF (Export Dashboard)", "callback_data": f"pdf_{today_for_search}"}],
        [{"text": "📅 ឆែកថ្ងៃផ្សេង (Specific Date)", "callback_data": "ask_specific_date"}]
    ]}

    if cache_key in report_cache:
        cached_msg = report_cache[cache_key]
        if cached_msg == "EMPTY":
            if requested_date_str or NOTIFY_EMPTY_DATA:
                send_simple_message(target_chat_id, f"📭 មិនមានទិន្នន័យសម្រាប់ថ្ងៃ <b>{today_for_display}</b> នេះទេ។")
            return
        send_simple_message(target_chat_id, cached_msg, keyboard)
        return

    # ទាញទិន្នន័យ
    report_data, is_success = fetch_report_data(target_date)
    if not is_success:
        send_simple_message(target_chat_id, "⚠️ <b>បញ្ហា:</b> មិនអាចភ្ជាប់ទៅកាន់ Google Sheet បានទេ។")
        return
        
    if not report_data['has_data']:
        report_cache[cache_key] = "EMPTY"
        if requested_date_str or NOTIFY_EMPTY_DATA:
            send_simple_message(target_chat_id, f"📭 មិនមានទិន្នន័យសម្រាប់ថ្ងៃ <b>{today_for_display}</b> នេះទេ។")
        return

    # តម្រៀបសារ Text ធម្មតា
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
# Webhook Route (ទទួលសំណើពី Telegram)
# ==========================================
@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if not update: return jsonify({"status": "ok"})

    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        chat_id = cb["message"]["chat"]["id"]
        message_id = cb["message"]["message_id"]
        data = cb["data"]

        threading.Thread(target=answer_callback, args=(cb_id,)).start()

        # ប្រសិនបើចុចប៊ូតុង ទាញយក PDF 
        if data.startswith('pdf_'):
            selected_date = data.replace('pdf_', '')
            threading.Thread(target=send_simple_message, args=(chat_id, f"📥 កំពុងបង្កើតឯកសារ PDF សម្រាប់ថ្ងៃ <b>{selected_date}</b> ...")).start()
            # ដំណើរការបង្កើត PDF រួចផ្ញើចេញ
            threading.Thread(target=generate_and_send_pdf, args=(selected_date, chat_id)).start()

        # ប្រសិនបើចុចប៊ូតុង ឆែកថ្ងៃផ្សេង
        elif data in ['ask_specific_date', 'back_to_months']:
            current_year = datetime.now(tz).year
            month_names = ["មករា (Jan)", "កុម្ភៈ (Feb)", "មីនា (Mar)", "មេសា (Apr)", "ឧសភា (May)", "មិថុនា (Jun)", 
                           "កក្កដា (Jul)", "សីហា (Aug)", "កញ្ញា (Sep)", "តុលា (Oct)", "វិច្ឆិកា (Nov)", "ធ្នូ (Dec)"]
            keyboard_rows = []
            current_row = []
            for i in range(12):
                month_num = f"{i+1:02d}"
                current_row.append({"text": month_names[i], "callback_data": f"month_{current_year}-{month_num}"})
                if len(current_row) == 3 or i == 11:
                    keyboard_rows.append(current_row)
                    current_row = []
            inline_kb = {"inline_keyboard": keyboard_rows}
            msg = f"📅 សូមជ្រើសរើស <b>ខែ</b> ក្នុងឆ្នាំ {current_year} ៖"
            
            if data == 'back_to_months': threading.Thread(target=edit_message_text, args=(chat_id, message_id, msg, inline_kb)).start()
            else: threading.Thread(target=send_simple_message, args=(chat_id, msg, inline_kb)).start()

        # ប្រសិនបើចុចរើសខែ
        elif data.startswith('month_'):
            selected_month = data.replace('month_', '')
            year, month = map(int, selected_month.split('-'))
            days_in_month = calendar.monthrange(year, month)[1]
            keyboard_rows = []
            current_row = []
            for i in range(1, days_in_month + 1):
                day_num = f"{i:02d}"
                date_value = f"{selected_month}-{day_num}"
                current_row.append({"text": str(i), "callback_data": f"report_{date_value}"})
                if len(current_row) == 5 or i == days_in_month:
                    keyboard_rows.append(current_row)
                    current_row = []
            keyboard_rows.append([{"text": "⬅️ ត្រឡប់ក្រោយ (Back)", "callback_data": "back_to_months"}])
            inline_kb = {"inline_keyboard": keyboard_rows}
            msg = f"📅 សូមជ្រើសរើស <b>ថ្ងៃទី</b> សម្រាប់ខែ {selected_month} ៖"
            threading.Thread(target=edit_message_text, args=(chat_id, message_id, msg, inline_kb)).start()

        # ប្រសិនបើចុចរើសថ្ងៃ
        elif data.startswith('report_'):
            selected_date = data.replace('report_', '')
            msg = f"⏳ កំពុងស្វែងរកទិន្នន័យសម្រាប់ថ្ងៃ <b>{selected_date}</b> ..."
            threading.Thread(target=edit_message_text, args=(chat_id, message_id, msg)).start()
            threading.Thread(target=generate_and_send_report, args=(selected_date, chat_id)).start()

    # ខ. វាយបញ្ចូលអត្ថបទ
    elif "message" in update and "text" in update["message"]:
        text = update["message"]["text"].strip()
        chat_id = update["message"]["chat"]["id"]
        
        date_match = re.search(r'\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}-[a-zA-Z]{3}-\d{2,4}\b', text)
        if date_match:
            extracted_date = date_match.group(0)
            if text.startswith('/report') or ("វាយបញ្ចូលថ្ងៃខែ" in update["message"].get("reply_to_message", {}).get("text", "")):
                send_simple_message(chat_id, f"⏳ កំពុងស្វែងរកទិន្នន័យសម្រាប់ថ្ងៃ <b>{extracted_date}</b> ...")
                threading.Thread(target=generate_and_send_report, args=(extracted_date, chat_id)).start()

    return jsonify({"status": "ok"})


@app.route('/clear_cache', methods=['GET', 'POST'])
def clear_cache():
    global report_cache, CACHE_VERSION
    report_cache = {}
    CACHE_VERSION += 1
    return jsonify({"status": "success", "message": f"Cache cleared. New version: {CACHE_VERSION}"})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
