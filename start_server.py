import subprocess
import sys
import time
import os

print("=" * 60)
print("🚀 กำลังเริ่มต้นระบบผู้ช่วย LINE สรุปรายงานรายสัปดาห์...")
print("=" * 60)

# 1. เริ่ม FastAPI Server
server_cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
server_process = subprocess.Popen(server_cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

print("✅ เซิร์ฟเวอร์ภายในเริ่มทำงานที่: http://localhost:8000")
print("⏳ กำลังสร้างลิงก์ Public Webhook HTTPS สำหรับ LINE...")

# 2. เริ่ม Localtunnel ผ่าน npx
tunnel_cmd = ["cmd", "/c", "npx", "--yes", "localtunnel", "--port", "8000"]
tunnel_process = subprocess.Popen(tunnel_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

webhook_url = None
for line in iter(tunnel_process.stdout.readline, ''):
    print(line, end='')
    if "your url is:" in line.lower():
        url = line.split("your url is:")[-1].strip()
        webhook_url = f"{url}/webhook"
        break

print("\n" + "=" * 60)
if webhook_url:
    print("🎉 สร้าง Webhook URL สำเร็จแล้ว!")
    print(f"👉 กรุณานำลิงก์นี้ไปวางในช่อง 'ลิงก์ Webhook' ใน LINE:")
    print(f"\n   {webhook_url}\n")
    print("จากนั้นกดปุ่ม [บันทึก] และเปิดสวิตช์ [Use Webhook]")
else:
    print("💡 หากไม่แสดง URL สามารถรันคำสั่งนี้ในอีกหน้าต่างได้: npx localtunnel --port 8000")
print("=" * 60)
print("\n(กด Ctrl + C เพื่อหยุดการทำงานของระบบ)\n")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nกำลังปิดระบบ...")
    server_process.terminate()
    tunnel_process.terminate()
