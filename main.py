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

app = Flask(__name__)

# ១. ទាញយកព័ត៌មានពី Environment Variables របស់ Render
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'ដាក់_TOKEN_ទីនេះ')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', 'ដាក់_CHAT_ID_ទីនេះ')
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', 'ដាក់_ID_Sheet_ទីនេះ')

# ទាមទារ Service Account JSON ពី Google Cloud (ដាក់ជាអក្សរ JSON ចូលក្នុង Render Env)
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON', '')

TARGET_PAGES = ['Main Page', 'Sovanna']
NOTIFY_EMPTY_DATA = True

# តំបន់ពេលវេលា (Timezone) នៅកម្ពុជា
tz = pytz.timezone('Asia/Phnom_Penh')

# ប្រព័ន្ធ Cache (រក្សាទុកក្នុង RAM របស់ Render)
report_cache = {}
CACHE_VERSION = 1

# ==========================================
# មុខងារភ្ជាប់ទៅកាន់ Telegram API 
# ==========================================
def telegram_api(method, payload):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
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

# ==========================================
# មុខងារភ្ជាប់ទៅកាន់ Google Sheet API
# ==========================================
def get_google_sheet():
    if not GOOGLE_CREDENTIALS_JSON:
        print("⚠️ មិនមាន GOOGLE_CREDENTIALS_JSON ទេ។")
        return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        print("⚠️ បញ្ហាភ្ជាប់ Google Sheet:", e)
        return None

# ==========================================
# មុខងារទាញយក និងបញ្ជូនរបាយការណ៍ (រត់ជា Background Thread)
# ==========================================
def generate_and_send_report(requested_date_str, target_chat_id):
    global report_cache
    
    # ស្វែងរកកាលបរិច្ឆេទ
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
    today_month_search = target_date.strftime("%Y-%m")
    today_for_display = target_date.strftime("%d/%m/%Y")
    
    cache_key = f"REPORT_v{CACHE_VERSION}_{today_for_search}"
    
    # ឆែកមើល Cache ក្នុង RAM របស់ Python
    if cache_key in report_cache:
        cached_msg = report_cache[cache_key]
        print(f"⚡ ទាញពី Python Cache យ៉ាងលឿន: {today_for_search}")
        if cached_msg == "EMPTY":
            if requested_date_str or NOTIFY_EMPTY_DATA:
                send_simple_message(target_chat_id, f"📭 មិនមានទិន្នន័យសម្រាប់ថ្ងៃ <b>{today_for_display}</b> នេះទេ។")
            return
        
        keyboard = {"inline_keyboard": [[{"text": "📅 ឆែកថ្ងៃផ្សេង (Specific Date)", "callback_data": "ask_specific_date"}]]}
        send_simple_message(target_chat_id, cached_msg, keyboard)
        return

    # ដំណើរការទាញទិន្នន័យពី Sheet
    print("🔍 កំពុងទាញពី Google Sheet API...")
    ss = get_google_sheet()
    if not ss:
        send_simple_message(target_chat_id, "⚠️ <b>បញ្ហា:</b> មិនអាចភ្ជាប់ទៅកាន់ Google Sheet បានទេ។ សូមពិនិត្យ Service Account JSON។")
        return

    message = f"Dear Management\n💻 Marketing Sale Report\n🗓️ Date (<b>{today_for_display}</b>)\n\n"
    has_data = False

    target_y = target_date.year
    target_m = target_date.month
    target_d = target_date.day

    for page_name in TARGET_PAGES:
        try:
            worksheet = ss.worksheet(page_name)
            data = worksheet.get_all_values()
        except gspread.exceptions.WorksheetNotFound:
            continue
        
        if len(data) <= 4:
            continue

        target_string = str(data[2][0]) if len(data[2]) > 0 else "0"
        match = re.search(r'\d+(\.\d+)?', target_string)
        target_amount = float(match.group(0)) if match else 0.0
        
        days_in_month = calendar.monthrange(target_date.year, target_date.month)[1]
        daily_target = target_amount / days_in_month if days_in_month else 0

        num_chat = online_booking = visit = close_deal = package_count = 0
        total_sale_amount = current_month_sale_amount = 0.0

        for i in range(4, len(data)):
            row = data[i]
            if len(row) < 2 or not row[1]:
                continue
            
            str_date = str(row[1]).strip()
            is_match_day = False
            is_match_month = False

            if str_date.startswith(today_month_search):
                is_match_month = True
            
            if str_date == today_for_search:
                is_match_month = True
                is_match_day = True
            elif not is_match_month:
                try:
                    p_date = parser.parse(str_date)
                    if p_date.year == target_y and p_date.month == target_m:
                        is_match_month = True
                        if p_date.day == target_d:
                            is_match_day = True
                except:
                    pass

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
            if has_data: message += "========================\n\n"
            has_data = True

            rate_booking = f"{(online_booking / num_chat) * 100:.2f}" if num_chat > 0 else "0.00"
            rate_visit = f"{(visit / num_chat) * 100:.2f}" if num_chat > 0 else "0.00"
            rate_close_deal = f"{(close_deal / num_chat) * 100:.2f}" if num_chat > 0 else "0.00"
            rate_package = f"{(package_count / close_deal) * 100:.2f}" if close_deal > 0 else "0.00"
            rate_daily_amount = f"{(total_sale_amount / daily_target) * 100:.2f}" if daily_target > 0 else "0.00"
            rate_monthly_amount = f"{(current_month_sale_amount / target_amount) * 100:.2f}" if target_amount > 0 else "0.00"

            message += f"🌐 Page: <b>{page_name}</b>\n"
            message += f"» Number of Chat: <b>{num_chat}</b>\n"
            message += f"» Online Booking: <b>{online_booking}</b>\n"
            message += f"» Visit: <b>{visit}</b>\n"
            message += f"» Close Deal: <b>{close_deal}</b>\n"
            message += f"» Package: <b>{package_count}</b>\n"
            message += f"» Total Sale Amount (Today): <b>${total_sale_amount:.2f}</b>\n"
            message += f"» Total Sale Amount (Monthly): <b>${current_month_sale_amount:.2f}</b>\n\n"
            
            message += "♻️ Summary Conversion rate %\n"
            message += f"» Online Booking: <b>{rate_booking}%</b>\n"
            message += f"» Visit: <b>{rate_visit}%</b>\n"
            message += f"» Close Deal: <b>{rate_close_deal}%</b>\n"
            message += f"» Package: <b>{rate_package}%</b>\n"
            message += f"» Daily Sale Amount: <b>{rate_daily_amount}%</b>\n"
            message += f"» Monthly Sale Amount: <b>{rate_monthly_amount}%</b>\n\n"

    if not has_data:
        report_cache[cache_key] = "EMPTY"
        if requested_date_str or NOTIFY_EMPTY_DATA:
            send_simple_message(target_chat_id, f"📭 មិនមានទិន្នន័យសម្រាប់ថ្ងៃ <b>{today_for_display}</b> នេះទេ។")
        return

    message += "Thank you 😊"
    report_cache[cache_key] = message
    
    keyboard = {"inline_keyboard": [[{"text": "📅 ឆែកថ្ងៃផ្សេង (Specific Date)", "callback_data": "ask_specific_date"}]]}
    send_simple_message(target_chat_id, message, keyboard)


# ==========================================
# Webhook Route (ទទួលសំណើពី Telegram)
# ==========================================
@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if not update:
        return jsonify({"status": "ok"})

    # ក. ចុចលើ Button
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        chat_id = cb["message"]["chat"]["id"]
        message_id = cb["message"]["message_id"]
        data = cb["data"]

        # បិទ Loading ភ្លាមៗ (Parallel) ក្នុង Python ប្រើ Thread រត់ចោល
        threading.Thread(target=answer_callback, args=(cb_id,)).start()

        if data in ['ask_specific_date', 'back_to_months']:
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
            
            if data == 'back_to_months':
                threading.Thread(target=edit_message_text, args=(chat_id, message_id, msg, inline_kb)).start()
            else:
                threading.Thread(target=send_simple_message, args=(chat_id, msg, inline_kb)).start()

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

        elif data.startswith('report_'):
            selected_date = data.replace('report_', '')
            msg = f"⏳ កំពុងស្វែងរកទិន្នន័យសម្រាប់ថ្ងៃ <b>{selected_date}</b> សូមរង់ចាំបន្តិច..."
            
            # ប្តូរ UI ទៅ "កំពុងស្វែងរក" លឿនដូចផ្លេកបន្ទោរ ព្រមជាមួយការទាញរបាយការណ៍ក្នុង Background (Asynchronous)
            threading.Thread(target=edit_message_text, args=(chat_id, message_id, msg)).start()
            threading.Thread(target=generate_and_send_report, args=(selected_date, chat_id)).start()

    # ខ. វាយបញ្ចូលអត្ថបទ
    elif "message" in update and "text" in update["message"]:
        text = update["message"]["text"].strip()
        chat_id = update["message"]["chat"]["id"]
        
        date_match = re.search(r'\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}-[a-zA-Z]{3}-\d{2,4}\b', text)
        if date_match:
            extracted_date = date_match.group(0)
            if text.startswith('/report'):
                send_simple_message(chat_id, f"⏳ កំពុងស្វែងរកទិន្នន័យសម្រាប់ថ្ងៃ <b>{extracted_date}</b> ...")
                threading.Thread(target=generate_and_send_report, args=(extracted_date, chat_id)).start()
            else:
                reply_to = update["message"].get("reply_to_message", {})
                is_reply = "វាយបញ្ចូលថ្ងៃខែ" in reply_to.get("text", "")
                if is_reply or extracted_date in text:
                    send_simple_message(chat_id, f"⏳ កំពុងស្វែងរកទិន្នន័យសម្រាប់ថ្ងៃ <b>{extracted_date}</b> ...")
                    threading.Thread(target=generate_and_send_report, args=(extracted_date, chat_id)).start()

    return jsonify({"status": "ok"})


# Route សម្រាប់ជម្រះ Cache (យក URL នេះទៅដាក់ក្នុង Google Apps Script onEdit Trigger បាន)
@app.route('/clear_cache', methods=['GET', 'POST'])
def clear_cache():
    global report_cache, CACHE_VERSION
    report_cache = {}
    CACHE_VERSION += 1
    return jsonify({"status": "success", "message": f"Cache cleared. New version: {CACHE_VERSION}"})

if __name__ == '__main__':
    # ប្រើ Port ដែល Render ផ្តល់ឲ្យ
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
