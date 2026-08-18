# คู่มือรัน ERP AI Agent Prototype

คู่มือนี้สำหรับ Windows PowerShell โดยให้รันคำสั่งจากโฟลเดอร์:

```text
C:\Users\ANURAK\Desktop\Project ส่วนตัว\erp-ai-agent
```

## สถานะปัจจุบัน

- สร้าง `.venv` แล้ว
- ติดตั้ง dependencies จาก `requirements.txt` ใน `.venv` แล้ว
- มีไฟล์ `.env` สำหรับ configuration
- ชุดทดสอบไม่ต้องใช้ MongoDB หรือ OpenRouter จริง
- Mock data script เขียนเฉพาะฐานข้อมูลที่ระบุใน `MONGODB_DATABASE`

## 1. เข้าโฟลเดอร์โปรเจกต์

เปิด PowerShell แล้วรัน:

```powershell
cd "C:\Users\ANURAK\Desktop\Project ส่วนตัว\erp-ai-agent"
```

ไฟล์สำหรับรันทั้งหมดเป็น Python ไม่ใช้ไฟล์ `.ps1` หาก terminal ยังไม่ได้ใช้ `.venv` ให้เลือก Python interpreter เป็น `.venv\Scripts\python.exe` ใน IDE หรือ activate environment ก่อน จากนั้นทุกคำสั่งใช้ `python` ได้เหมือนกันทุก shell

ตรวจสอบ Python ใน `.venv`:

```powershell
python --version
```

## 2. ตรวจ `.env`

สำหรับใช้งาน `/chat` กับข้อมูล ERP จริง ต้องมีค่าหลักดังนี้:

```env
OPENROUTER_API_KEY=ใส่คีย์จริง
OPENROUTER_MODEL=deepseek/deepseek-v4-flash

MONGODB_URI=ใส่ MongoDB URI จริง
MONGODB_DATABASE=ใส่ชื่อฐานข้อมูลจริง
```

ค่าที่เหลือสามารถใช้ default:

```env
MAX_AGENT_RETRY=3
MAX_QUERY_LIMIT=100
MONGO_TIMEOUT_MS=5000
DEBUG_AGENT=true
DEBUG_LEVEL=full
PRINT_ANSWER_TO_CONSOLE=true
TRACE_DIR=.debug_traces
```

ข้อควรระวัง:

- อย่า commit หรือส่งไฟล์ `.env` ให้ผู้อื่น
- อย่าวาง API key หรือ MongoDB URI ลงใน metadata YAML
- `MONGODB_DATABASE` เป็นขอบเขตฐานข้อมูลที่ application และ mock script จะเลือกใช้

## 3. รัน Automated Tests

```powershell
python run_tests.py
```

ผลที่ควรเห็น:

```text
57 passed
```

Tests ใช้ fake LLM และ fake MongoDB จึงไม่เสียค่า OpenRouter และไม่แก้ข้อมูลในฐานข้อมูลจริง

## 4. สร้าง Mock Data

ค่าเริ่มต้นสร้าง rentals 48 รายการ กระจายในช่วง 5 วัน พร้อมข้อมูลที่เชื่อมโยงกัน:

```powershell
python run_mock_data.py
```

จำนวนเอกสารโดยประมาณ:

```text
customers:                 20
vehicles:                  18
promotions:                 4
rentals:                   48
payments:                  48
maintenance:               12
incidents:                  8
charging_sessions:         24
installment_plans:          6
installment_schedules:     24
campaigns:                  4
campaign_engagements:      24
total:                    240
```

กำหนดจำนวน rentals และช่วงเวลา 3–5 วันเองได้:

```powershell
python run_mock_data.py --rentals 45 --days 4
```

Mock script มีพฤติกรรมดังนี้:

- ใช้ `MONGODB_SEED_URI` ถ้ามี มิฉะนั้นใช้ `MONGODB_URI`
- เลือกเขียนเฉพาะ `MONGODB_DATABASE`
- ใช้ deterministic `_id` และ upsert
- รันซ้ำด้วยค่าเดิมแล้วจะอัปเดต mock records ชุดเดิม
- ไม่ลบข้อมูลเดิม
- ไม่ drop collection
- ไม่พิมพ์ credential ลง terminal

เมื่อสำเร็จจะเห็นข้อความลักษณะนี้:

```text
Seed completed for database: ชื่อฐานข้อมูล
  customers: 20 mock documents upserted
  promotions: 4 mock documents upserted
  vehicles: 18 mock documents upserted
  rentals: 48 mock documents upserted
  payments: 48 mock documents upserted
  maintenance: 12 mock documents upserted
  total: 150 linked documents
```

## 5. เปิด FastAPI Server

```powershell
python run_server.py
```

เมื่อ server พร้อม จะเห็น URL ประมาณ:

```text
http://127.0.0.1:8000
```

หน้าใช้งานสำหรับ development:

- Operations Dashboard: `http://127.0.0.1:8000/`
- Health check: `http://127.0.0.1:8000/health`
- Swagger API: `http://127.0.0.1:8000/docs`
- Debug UI: `http://127.0.0.1:8000/debug-ui`

Dashboard อ่านข้อมูลจาก MongoDB แบบ read-only และไม่เรียก OpenRouter จึงไม่มีค่าใช้จ่าย LLM
เมื่อกดรีเฟรช หน้าเว็บจะโหลดข้อมูลล่าสุดจาก `GET /api/dashboard`

หยุด server ด้วย `Ctrl+C`

เมื่อ `PRINT_ANSWER_TO_CONSOLE=true` ทุกครั้งที่ `/chat` ตอบสำเร็จ หน้าต่าง Server จะแสดงคำถามและคำตอบด้วย newline จริง จึงไม่เห็น `\n` แบบ JSON ใน Postman

## 6. ทดสอบ Health Check

เปิด PowerShell อีกหน้าต่างแล้วรัน:

```powershell
curl.exe "http://127.0.0.1:8000/health"
```

ผลที่คาดหวัง:

```json
{"status":"ok"}
```

## 7. ทดสอบ Chat API

วิธีอ่านง่ายที่สุดคือถามผ่าน command line:

```powershell
python ask.py "รถทะเบียน กข1234 ตอนนี้ใครเช่าอยู่"
```

แสดง `request_id` เพิ่มด้วย:

```powershell
python ask.py "โปรโมชั่น WELCOME3 มาจากอะไร" --show-request-id
```

หรือทดสอบ raw JSON response ด้วย curl:

```powershell
curl.exe -X POST "http://127.0.0.1:8000/chat" `
  -H "Content-Type: application/json" `
  -d '{"message":"รถทะเบียน กข1234 ตอนนี้ใครเช่าอยู่"}'
```

คำถามตัวอย่างสำหรับ mock data:

```text
รถทะเบียน กข1234 ตอนนี้ใครเช่าอยู่
ลูกค้ารหัส CUS-0004 ทำไมได้วันเช่าเพิ่ม 7 วัน
ลูกค้ารหัส CUS-0003 มียอดค้างชำระอะไรบ้าง
รถทะเบียน งจ1238 เคยซ่อมอะไรบ้าง
โปรโมชั่น WELCOME3 มาจากอะไร
ลูกค้ารหัส CUS-0001 เช่ารถกี่ครั้งแล้ว
```

หมายเหตุ: คำตอบจริงขึ้นอยู่กับ record ที่สัมพันธ์กับทะเบียนหรือรหัสนั้น หากไม่พบข้อมูล Agent ควรตอบว่าข้อมูลไม่เพียงพอแทนการเดา

## 8. ดู Debug Trace

เมื่อ `DEBUG_AGENT=true` ผลจาก `/chat` จะมี `debug.request_id` เช่น:

```text
req_abc123...
```

เปิด trace ผ่าน browser:

```text
http://127.0.0.1:8000/debug/req_abc123...
```

หรือใส่ request ID ในหน้า:

```text
http://127.0.0.1:8000/debug-ui
```

Trace files ถูกเก็บใน `.debug_traces` และไม่ควรมี API key หรือ MongoDB URI

## 9. ติดตั้ง Dependencies ใหม่

ปกติไม่ต้องทำอีก เพราะ `.venv` พร้อมแล้ว หากมีการแก้ `requirements.txt` ให้รัน:

```powershell
python -m pip install -r requirements.txt
```

## 10. ปัญหาที่พบบ่อย

### `OPENROUTER_API_KEY is not configured`

ตรวจว่ามีไฟล์ `.env` อยู่ที่ root ของโปรเจกต์ และชื่อ variable ถูกต้อง

### HTTP 502 `Agent processing failed`

ตรวจตามลำดับนี้:

1. ดู traceback และบรรทัด `agent_request_failed` ในหน้าต่างที่รัน `python run_server.py`
2. อ่าน `detail.request_id`, `detail.error_type` และ `detail.error` จาก response เมื่อ `DEBUG_AGENT=true`
3. เปิด `http://127.0.0.1:8000/debug/<request_id>` หรือใส่ request ID ใน `/debug-ui`
4. เปิดไฟล์ `.debug_traces\<request_id>.json` หากต้องการดู trace โดยตรง

ตัวอย่าง response:

```json
{
  "detail": {
    "message": "Agent processing failed; inspect server logs or debug trace",
    "request_id": "req_...",
    "error_type": "OpenRouterError",
    "error": "OpenRouter HTTP 401: ...",
    "debug_url": "/debug/req_..."
  }
}
```

รหัสที่พบบ่อย:

```text
401  OpenRouter API key ไม่ถูกต้องหรือไม่มีสิทธิ์
402  เครดิต OpenRouter ไม่เพียงพอ
404  Model ID ไม่พบ
429  Rate limit
5xx  Provider/OpenRouter มีปัญหาชั่วคราว
```

### `MONGODB_URI and MONGODB_DATABASE must be configured`

ตรวจสองค่าดังกล่าวใน `.env` แล้ว restart Uvicorn

### MongoDB authentication หรือ timeout

- ตรวจ username/password และ `authSource` ใน URI
- ตรวจ network/firewall หรือ MongoDB Atlas IP allowlist
- ตรวจว่า user เข้าถึง database ใน `MONGODB_DATABASE` ได้

### คำสั่ง `python` ไม่ได้ใช้ `.venv`

เลือก interpreter ของโปรเจกต์เป็น `.venv\Scripts\python.exe` ใน IDE หรือเปิด Command Prompt แล้ว activate ด้วย `.venv\Scripts\activate.bat` จากนั้นตรวจด้วย `python --version`

### Port 8000 ถูกใช้งานอยู่

เปลี่ยน port:

```powershell
python run_server.py --port 8001
```

แล้วใช้ URL `http://127.0.0.1:8001`

## ลำดับรันแบบสั้น

```powershell
cd "C:\Users\ANURAK\Desktop\Project ส่วนตัว\erp-ai-agent"
python run_tests.py
python run_mock_data.py
python run_server.py
```
