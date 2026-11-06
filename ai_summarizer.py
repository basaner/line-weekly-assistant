import os
from datetime import datetime
from google import genai
from google.genai import types

def summarize_weekly_report(group_name: str, messages: list, gemini_api_key: str = None) -> str:
    """
    สรุปรายงานการทำงานประจำสัปดาห์จากประวัติข้อความ
    """
    if not messages:
        return f"📋 รายงานประจำสัปดาห์: {group_name}\nช่วง 7 วันที่ผ่านมา ยังไม่มีข้อมูลการสนทนาเกี่ยวกับงานที่บันทึกไว้ครับ"

    # จัดเตรียมข้อความ
    formatted_logs = []
    for m in messages:
        sender = m.get("user_name") or "สมาชิก"
        text = m.get("message_text", "").strip()
        time_str = m.get("created_at", "")[:16]
        formatted_logs.append(f"[{time_str}] {sender}: {text}")

    full_conversation = "\n".join(formatted_logs)

    # กรณีมี Gemini API Key ให้ใช้ AI สรุปอย่างชาญฉลาด
    api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        prompt = f"""
คุณเป็นเลขานุการและผู้ช่วยบริหารโครงการมืออาชีพ จงวิเคราะห์และสรุปบทสนทนาการทำงานประจำสัปดาห์ของกลุ่ม: "{group_name}"

โปรดจัดทำรายงานสรุปภาษาไทยที่มีรูปแบบอ่านง่าย กระชับ ชัดเจน โดยแบ่งหมวดหมู่ตามนี้:

📊 รายงานสรุปการทำงานประจำสัปดาห์ ({group_name})
ประจำวันที่: {datetime.now().strftime('%d/%m/%Y')}

1. 🎯 งานที่สำเร็จแล้ว / อัปเดตงานหลัก (Completed & Highlights):
- (สรุปสิ่งที่เสร็จแล้ว หรือความก้าวหน้าสำคัญ)

2. 🔄 งานที่กำลังดำเนินอยู่ (In Progress):
- (งานที่กำลังทำ ใครรับผิดชอบถ้ามีระบุ)

3. ⚠️ ปัญหา / อุปสรรค / จุดที่ต้องแก้ไข (Blockers & Issues):
- (ปัญหาหน้างาน, ข้อขัดข้อง, หรือเรื่องที่รอการอนุมัติ)

4. 📌 ข้อตกลงและสิ่งที่ต้องติดตามในสัปดาห์ถัดไป (Action Items):
- (นัดหมาย, เดดไลน์, สิ่งที่ทีมต้องทำต่อ)

ข้อความบทสนทนาในกลุ่ม:
\"\"\"
{full_conversation}
\"\"\"

หมายเหตุ:
- คัดกรองข้อความที่เป็นการทักทายหรือคุยเล่นทั่วไปออก
- เน้นข้อเท็จจริง วันที่ ตัวเลข หรือชื่อผู้รับผิดชอบ (ถ้ามี)
- ใช้ข้อความกระชับ อ่านง่าย เหมาะกับการอ่านใน LINE
"""
        # วิธีที่ 1: เรียกผ่าน REST API โดยตรง (รองรับคีย์ AQ. ได้ 100% แน่นอน)
        try:
            import requests
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={api_key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            res = requests.post(url, json=payload, timeout=20)
            if res.status_code == 200:
                data = res.json()
                text = data['candidates'][0]['content']['parts'][0]['text']
                if text:
                    return text.strip()
            else:
                print(f"[REST Gemini Error] {res.status_code}: {res.text}")
        except Exception as e:
            print(f"[REST Exception] {e}")

        # วิธีที่ 2: เรียกผ่าน SDK สำรอง
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model='gemini-flash-latest',
                contents=prompt,
            )
            if response.text:
                return response.text.strip()
        except Exception as e:
            print(f"[AI Summarizer SDK Error] {e}")

    # Fallback กรณีไม่ได้ใส่ Gemini API Key หรือเรียก API ไม่สำเร็จ
    header = f"📊 รายงานสรุปการทำงานประจำสัปดาห์: {group_name}\n"
    header += f"📅 ข้อมูลช่วง 7 วันล่าสุด ({len(messages)} ข้อความที่บันทึก)\n\n"
    header += "💡 (เพื่อการสรุปแบบแยกหมวดหมู่อัตโนมัติด้วย AI กรุณาใส่ GEMINI_API_KEY ในไฟล์ .env)\n\n"
    header += "📝 บันทึกประเด็นงานสำคัญล่าสุด:\n"
    
    # แสดงตัวอย่างข้อความเด่นๆ ไม่เกิน 15 ข้อความ
    sample = messages[-15:]
    for m in sample:
        sender = m.get("user_name") or "สมาชิก"
        text = m.get("message_text", "")
        time_str = m.get("created_at", "")[:16]
        header += f"• [{time_str}] {sender}: {text}\n"

    return header
