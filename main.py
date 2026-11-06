import os
import uvicorn
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent, JoinEvent
from google import genai
from google.genai import types
from apscheduler.schedulers.background import BackgroundScheduler

from database import (
    init_db,
    register_group,
    is_group_registered,
    get_registered_groups,
    save_message,
    get_weekly_messages,
    count_weekly_messages
)
from ai_summarizer import summarize_weekly_report

# ----------------- CONFIGURATION -----------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GOOGLE_SHEET_WEBHOOK_URL = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
PORT = int(os.getenv("PORT", "8000"))

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = FastAPI(title="LINE Weekly Report Assistant")

# เริ่มต้นฐานข้อมูล
init_db()

# ----------------- HELPER FUNCTIONS -----------------
def get_user_display_name(api_client, group_id: str, user_id: str) -> str:
    """ดึงชื่อโปรไฟล์ของสมาชิกในกลุ่ม"""
    try:
        line_bot_api = MessagingApi(api_client)
        if group_id:
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
            return profile.display_name
        else:
            profile = line_bot_api.get_profile(user_id)
            return profile.display_name
    except Exception:
        return "สมาชิก"

def get_group_title(api_client, group_id: str) -> str:
    """ดึงชื่อกลุ่ม LINE"""
    try:
        line_bot_api = MessagingApi(api_client)
        summary = line_bot_api.get_group_summary(group_id)
        return summary.group_name
    except Exception:
        return "กลุ่มงาน"

def send_reply(reply_token: str, text: str):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            )
        )

def push_to_group(group_id: str, text: str):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.push_message(
            PushMessageRequest(
                to=group_id,
                messages=[TextMessage(text=text)]
            )
        )

def send_to_google_sheet(group_name: str, user_name: str, message: str):
    """ส่งข้อมูลบันทึกลง Google Sheets แบบเรียลไทม์ (ถ้ามีการตั้งค่า URL)"""
    if not GOOGLE_SHEET_WEBHOOK_URL:
        return
    try:
        import requests
        payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "group_name": group_name,
            "user_name": user_name,
            "message": message
        }
        requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[Google Sheet Sync Error] {e}")

# ----------------- LINE WEBHOOK -----------------
@app.post("/webhook")
async def line_webhook(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    body_str = body.decode("utf-8")
    try:
        handler.handle(body_str, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

# เมื่อบอทถูกเชิญเข้ากลุ่ม
@handler.add(JoinEvent)
def handle_join(event):
    group_id = getattr(event.source, "group_id", None)
    if group_id:
        with ApiClient(configuration) as api_client:
            g_name = get_group_title(api_client, group_id)
            register_group(group_id, g_name)
            welcome_msg = (
                f"สวัสดีครับทุกคนในกลุ่ม '{g_name}'! 👋\n"
                f"ผมคือผู้ช่วยจดบันทึกการทำงานและสรุปรายงานรายสัปดาห์\n\n"
                f"✅ ได้เปิดโหมดบันทึกข้อมูลกลุ่มนี้ให้อัตโนมัติแล้วครับ\n\n"
                f"คำสั่งที่มีให้ใช้งาน:\n"
                f"• พิมพ์ '#สรุปงาน' หรือ '#สรุปสัปดาห์นี้' เพื่อดูรายงานทันที\n"
                f"• พิมพ์ '#สถานะ' เพื่อดูจำนวนข้อความที่บันทึก\n"
                f"• ระบบจะส่งรายงานสรุปอัตโนมัติทุกเย็นวันศุกร์ (17:00 น.)"
            )
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=welcome_msg)]
                )
            )

# เมื่อมีข้อความตัวหนังสือเข้ามา
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    source_type = event.source.type
    group_id = getattr(event.source, "group_id", None) or getattr(event.source, "room_id", None)
    user_id = getattr(event.source, "user_id", None)

    # 1. จัดการคำสั่งควบคุม (Commands)
    if text in ["#ลงทะเบียนกลุ่มงาน", "#เริ่มบันทึก", "#ลงทะเบียน"]:
        if not group_id:
            send_reply(event.reply_token, "⚠️ คำสั่งนี้ต้องใช้ภายในกลุ่ม LINE ครับ")
            return
        with ApiClient(configuration) as api_client:
            g_name = get_group_title(api_client, group_id)
            register_group(group_id, g_name)
            send_reply(
                event.reply_token,
                f"✅ ลงทะเบียนกลุ่ม '{g_name}' สำเร็จ!\nบอทจะเริ่มบันทึกข้อมูลการทำงานเพื่อจัดทำ Weekly Report ให้ครับ"
            )
        return

    if text in ["#สถานะ", "#status"]:
        if not group_id:
            send_reply(event.reply_token, "⚠️ กรุณาพิมพ์ในกลุ่มงานครับ")
            return
        is_reg = is_group_registered(group_id)
        if not is_reg:
            send_reply(event.reply_token, "❌ กลุ่มนี้ยังไม่ได้เปิดใช้งาน พิมพ์ '#ลงทะเบียน' เพื่อเริ่มครับ")
            return
        cnt = count_weekly_messages(group_id, days=7)
        status_msg = (
            f"📊 สถานะผู้ช่วยกลุ่มงาน:\n"
            f"• สถานะ: บันทึกข้อมูลอยู่ (Active)\n"
            f"• ข้อมูลงานสะสม 7 วันล่าสุด: {cnt} ข้อความ\n"
            f"• กำหนดส่งรายงานอัตโนมัติ: ทุกวันศุกร์ 17:00 น.\n\n"
            f"(พิมพ์ '#สรุปงาน' เพื่อรับรายงานสรุป ณ ตอนนี้ได้ทันที)"
        )
        send_reply(event.reply_token, status_msg)
        return

    if text in ["#สรุปงาน", "#สรุปสัปดาห์นี้", "#weekly_report", "#report"]:
        if not group_id:
            send_reply(event.reply_token, "⚠️ กรุณาเรียกคำสั่งนี้ภายในกลุ่มงานครับ")
            return
        send_reply(event.reply_token, "⏳ กำลังประมวลผลและจัดทำรายงานสรุปประจำสัปดาห์ กรุณารอสักครู่ครับ...")
        # ดึงประวัติและสร้างรายงาน
        messages = get_weekly_messages(group_id, days=7)
        with ApiClient(configuration) as api_client:
            g_name = get_group_title(api_client, group_id)
        report = summarize_weekly_report(g_name, messages, GEMINI_API_KEY)
        push_to_group(group_id, report)
        return

    if text in ["#ช่วยเหลือ", "#help"]:
        help_msg = (
            "🤖 รายการคำสั่งของผู้ช่วยกลุ่มงาน:\n"
            "• #สรุปงาน : สรุปรายงานรายสัปดาห์ทันที\n"
            "• #สถานะ : ตรวจสอบจำนวนข้อความที่บันทึก\n"
            "• #เริ่มบันทึก : เปิดการบันทึกกลุ่มนี้\n"
            "• #ช่วยเหลือ : ดูคำแนะนำการใช้งาน"
        )
        send_reply(event.reply_token, help_msg)
        return

    # 2. บันทึกข้อความการทำงานทั่วไป
    # ต้องเป็นข้อความจากกลุ่มที่ลงทะเบียนไว้เท่านั้น (หรือถ้ายังไม่เคยลงทะเบียนแต่พิมพ์คุยในกลุ่ม เราสามารถเปิด auto-register ได้)
    if group_id:
        if not is_group_registered(group_id):
            # บันทึกกลุ่มงานนี้ให้อัตโนมัติทันที
            with ApiClient(configuration) as api_client:
                g_name = get_group_title(api_client, group_id)
                register_group(group_id, g_name)

        # กรอง Noise: ข้อความสั้นมากๆ หรือทักทายทั่วไป ไม่ต้องเก็บ
        ignored_keywords = ["555", "ok", "โอเค", "ครับ", "ค่ะ", "หวัดดี", "สวัสดี", "thx", "thanks"]
        if len(text) < 4 and text.lower() in ignored_keywords:
            return

        with ApiClient(configuration) as api_client:
            user_name = get_user_display_name(api_client, group_id, user_id) if user_id else "สมาชิก"

        save_message(group_id, user_id or "unknown", user_name, text)
        send_to_google_sheet(g_name, user_name, text)

# เมื่อมีคนส่งรูปภาพเข้ามาในกลุ่ม
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    group_id = getattr(event.source, "group_id", None) or getattr(event.source, "room_id", None)
    user_id = getattr(event.source, "user_id", None)

    if not group_id:
        return

    # ดึงชื่อกลุ่มและชื่อผู้ส่ง
    with ApiClient(configuration) as api_client:
        g_name = get_group_title(api_client, group_id)
        if not is_group_registered(group_id):
            register_group(group_id, g_name)
        user_name = get_user_display_name(api_client, group_id, user_id) if user_id else "สมาชิก"

    # 1. โหลดข้อมูลรูปภาพจาก LINE
    try:
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            image_bytes = blob_api.get_message_content(event.message.id)
    except Exception as e:
        print(f"[LINE Blob Error] {e}")
        return

    # 2. ให้ Gemini วิเคราะห์รูปภาพ (พร้อมคัดกรอง Noise)
    analysis_text = ""
    if GEMINI_API_KEY:
        prompt_img = (
            "วิเคราะห์รูปภาพนี้: หากเป็นภาพที่เกี่ยวกับการทำงาน (เช่น เครื่องจักร, ชิ้นงาน, ข้อบกพร่อง, เอกสาร, "
            "กราฟ, รายงาน, หน้าจอ, หรือหน้างาน) จงสรุปประเด็นสำคัญ ตัวเลข หรือข้อความที่พบอย่างกระชับใน 1-2 ประโยค "
            "แต่หากเป็นภาพที่ไม่เกี่ยวกับการทำงานเลย (เช่น รูปอาหาร, ดอกไม้ทักทาย, มีมตลก, เซลฟี่) ให้ตอบกลับเพียงคำเดียวว่า 'IGNORE'"
        )
        # ลองทาง REST API โดยตรง
        try:
            import base64
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{
                    "parts": [
                        {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}},
                        {"text": prompt_img}
                    ]
                }]
            }
            res = requests.post(url, json=payload, timeout=20)
            if res.status_code == 200:
                res_json = res.json()
                res_text = res_json['candidates'][0]['content']['parts'][0]['text'].strip()
                if "IGNORE" in res_text.upper():
                    print(f"[Image Filtered] ภาพไม่เกี่ยวกับงาน (ข้ามการบันทึก)")
                    return
                analysis_text = res_text
        except Exception as e:
            print(f"[REST Vision Error] {e}")

        # ถ้า REST ไม่ผ่าน ลองทาง SDK
        if not analysis_text:
            try:
                client = genai.Client(api_key=GEMINI_API_KEY)
                response = client.models.generate_content(
                    model='gemini-flash-latest',
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                        prompt_img
                    ]
                )
                res_text = response.text.strip() if response.text else ""
                if "IGNORE" in res_text.upper():
                    print(f"[Image Filtered] ภาพไม่เกี่ยวกับงาน (ข้ามการบันทึก)")
                    return
                analysis_text = res_text
            except Exception as e:
                print(f"[Gemini Vision Error] {e}")

    if not analysis_text:
        analysis_text = "ภาพถ่ายประกอบงาน"

    full_record = f"[📷 รูปภาพ] {analysis_text}"
    print(f"[{g_name}] {user_name}: {full_record}")

    # 3. บันทึกลง SQLite และ Google Sheets
    save_message(group_id, user_id or "unknown", user_name, full_record)
    send_to_google_sheet(g_name, user_name, full_record)

# ----------------- ตั้งเวลารันอัตโนมัติ (SCHEDULER: เวลาไทย Asia/Bangkok) -----------------
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")
scheduler = BackgroundScheduler(timezone=BANGKOK_TZ)

def auto_weekly_broadcast():
    """ส่งรายงานสรุปอัตโนมัติทุกกลุ่มที่ลงทะเบียน ทุกวันศุกร์ 17:00 (เวลาไทย)"""
    groups = get_registered_groups()
    now_th = datetime.now(BANGKOK_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_th}] เริ่มกระบวนการส่งรายงานอัตโนมัติประจำวันศุกร์ 17:00 น. ทั้งหมด {len(groups)} กลุ่ม")
    for g in groups:
        gid = g["group_id"]
        gname = g["group_name"]
        try:
            messages = get_weekly_messages(gid, days=7)
            if messages:
                report = summarize_weekly_report(gname, messages, GEMINI_API_KEY)
                push_to_group(gid, report)
                print(f"ส่งรายงานกลุ่ม '{gname}' เรียบร้อย")
        except Exception as e:
            print(f"ไม่สามารถส่งรายงานกลุ่ม '{gname}': {e}")

# ตั้งให้รันทุกวันศุกร์ เวลา 17:00 น. (เวลาประเทศไทย UTC+7)
scheduler.add_job(auto_weekly_broadcast, 'cron', day_of_week='fri', hour=17, minute=0, timezone=BANGKOK_TZ)
scheduler.start()

# ----------------- WEB DASHBOARD -----------------
@app.get("/", response_class=HTMLResponse)
def index():
    groups = get_registered_groups()
    groups_html = ""
    for g in groups:
        cnt = count_weekly_messages(g['group_id'], 7)
        groups_html += f"""
        <tr class="border-b">
            <td class="p-3 font-semibold">{g['group_name']}</td>
            <td class="p-3 text-xs text-gray-500 font-mono">{g['group_id']}</td>
            <td class="p-3 text-center"><span class="bg-blue-100 text-blue-800 text-xs px-2.5 py-0.5 rounded-full font-bold">{cnt} ข้อความ</span></td>
            <td class="p-3 text-xs text-gray-500">{g['registered_at']}</td>
            <td class="p-3 text-center">
                <a href="/report/{g['group_id']}" target="_blank" class="bg-emerald-600 hover:bg-emerald-700 text-white text-xs px-3 py-1.5 rounded shadow">ดูตัวอย่างสรุปงาน</a>
            </td>
        </tr>
        """

    if not groups_html:
        groups_html = "<tr><td colspan='5' class='p-6 text-center text-gray-400'>ยังไม่มีกลุ่มงานที่ลงทะเบียน (ดึงบอทเข้ากลุ่มแล้วพิมพ์ '#ลงทะเบียน')</td></tr>"

    html = f"""
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ระบบผู้ช่วยสรุปรายงาน LINE รายสัปดาห์</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>body {{ font-family: 'Sarabun', sans-serif; }}</style>
    </head>
    <body class="bg-slate-100 min-h-screen text-slate-800 p-6">
        <div class="max-w-5xl mx-auto space-y-6">
            <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200 flex justify-between items-center">
                <div>
                    <h1 class="text-2xl font-bold text-slate-800 flex items-center gap-2">
                        🤖 ผู้ช่วยบันทึก & สรุปรายงานการทำงาน LINE
                    </h1>
                    <p class="text-slate-500 text-sm mt-1">คัดกรองเฉพาะกลุ่มงาน บันทึกประเด็น และสรุปรายงาน Weekly Report อัตโนมัติ</p>
                </div>
                <div class="flex items-center gap-2">
                    <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800">
                        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                        ระบบพร้อมทำงาน
                    </span>
                </div>
            </div>

            <!-- กลุ่มงานที่ลงทะเบียน -->
            <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                <h2 class="text-lg font-bold text-slate-800 mb-4 flex items-center gap-2">
                    👥 กลุ่มงานที่กำลังบันทึกข้อมูล ({len(groups)} กลุ่ม)
                </h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm">
                        <thead class="bg-slate-50 text-slate-600 uppercase text-xs">
                            <tr>
                                <th class="p-3">ชื่อกลุ่ม</th>
                                <th class="p-3">Group ID</th>
                                <th class="p-3 text-center">ข้อความรอบ 7 วัน</th>
                                <th class="p-3">วันที่เริ่มลงทะเบียน</th>
                                <th class="p-3 text-center">จัดการ</th>
                            </tr>
                        </thead>
                        <tbody>
                            {groups_html}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- คำแนะนำการใช้งาน -->
            <div class="bg-blue-50 border border-blue-200 rounded-2xl p-6 text-sm text-blue-900 space-y-2">
                <h3 class="font-bold text-base text-blue-950 flex items-center gap-2">📌 คำแนะนำการเชื่อมต่อ:</h3>
                <p>1. เชิญบอท <strong>TanaBot</strong> เข้าไปในกลุ่มงานที่ต้องการ</p>
                <p>2. บอทจะเริ่มบันทึกข้อความเกี่ยวกับงานโดยอัตโนมัติ (กรองคำทักทายทั่วไปออกให้)</p>
                <p>3. หากต้องการดูสรุปทันที ให้พิมพ์ <strong><code>#สรุปงาน</code></strong> ในกลุ่ม LINE</p>
                <p>4. ระบบตั้งเวลาส่งรายงานอัตโนมัติทุก <strong>วันศุกร์ เวลา 17:00 น.</strong></p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.get("/report/{group_id}", response_class=HTMLResponse)
def preview_report(group_id: str):
    messages = get_weekly_messages(group_id, 7)
    report_text = summarize_weekly_report(f"กลุ่ม {group_id}", messages, GEMINI_API_KEY)
    html = f"""
    <!DOCTYPE html>
    <html lang="th">
    <head>
        <meta charset="UTF-8">
        <title>ตัวอย่างรายงานสรุปรายสัปดาห์</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600&display=swap" rel="stylesheet">
        <style>body {{ font-family: 'Sarabun', sans-serif; }}</style>
    </head>
    <body class="bg-slate-100 p-8">
        <div class="max-w-2xl mx-auto bg-white p-8 rounded-2xl shadow-sm border border-slate-200">
            <h1 class="text-xl font-bold mb-4">ตัวอย่าง Weekly Report</h1>
            <pre class="bg-slate-50 p-6 rounded-xl border border-slate-200 whitespace-pre-wrap text-sm text-slate-800 leading-relaxed">{report_text}</pre>
            <div class="mt-6 text-center">
                <a href="/" class="text-blue-600 text-sm hover:underline">← กลับไปหน้าหลัก</a>
            </div>
        </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
