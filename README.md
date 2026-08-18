# ERP AI Agent Prototype

Prototype ผู้ช่วยค้นข้อมูล ERP เช่ารถไฟฟ้าด้วยภาษาธรรมชาติ โดยใช้ metadata เป็น source of truth, สร้าง MongoDB query แบบ structured/read-only, validate ก่อน execute ทุกครั้ง และเก็บ debug trace ที่อธิบายการตัดสินใจแบบย่อโดยไม่เปิดเผย chain-of-thought หรือ secret

เอกสาร flow แบบละเอียดพร้อมตัวอย่างการอ่าน YAML และ payload ที่ส่งให้ model อยู่ใน [`WORKFLOW.md`](WORKFLOW.md)

ชุดคำถามสำหรับทดสอบกับ mock data พร้อม expected facts อยู่ใน [`TEST_QUESTIONS.md`](TEST_QUESTIONS.md)

## Architecture

```text
User / Debug UI
       │
       ▼
FastAPI  POST /chat
       │
       ▼
ERPAgentOrchestrator ───────────────────────────► JSON Trace Store
       │                                           (แยกจาก ERP)
       ├─ Intent Analysis ──► OpenRouter ──► DeepSeek V4 Flash
       ├─ search_metadata() ─► YAML metadata
       ├─ Logical Plan + Structured Mongo Query ─► OpenRouter
       ├─ validate_query() ──► schema / relationship / read-only guard
       ├─ execute_mongo_query() ─► read-only MongoDB user ─► ERP MongoDB
       ├─ bounded repair loop (MAX_AGENT_RETRY)
       └─ result validation + final answer ─► OpenRouter
```

FastAPI ใช้ explicit service-layer orchestration เพื่อควบคุม workflow และสร้าง trace ได้ครบทุกขั้น

## สิ่งที่รองรับ

- Intent/domain classification, metadata retrieval และ logical query plan แบบ Pydantic
- OpenRouter structured outputs ผ่าน JSON Schema และ model ที่เปลี่ยนได้ด้วย `OPENROUTER_MODEL`
- model เริ่มต้น `deepseek/deepseek-v4-flash` (DeepSeek V4 Flash)
- MongoDB operations: `find`, `findOne`, `aggregate`, `count`, `distinct`
- block write operations, `$where`, `$function`, `$out`, `$merge` และ aggregate stage ที่ไม่อนุญาต
- ตรวจ collection, field และ `$lookup` relationship จาก YAML
- enforce `MAX_QUERY_LIMIT`, timeout, BSON/ObjectId/datetime serialization
- retry/repair มีขอบเขตและหยุดเมื่อ query ที่ซ่อมไม่เปลี่ยน
- result validation: empty result หรือเหตุผลที่ไม่มีข้อมูลจะไม่ถูกแต่งขึ้น
- basic/full debug, “Why This Query?”, retry history, feedback และ secret redaction
- Debug UI ที่ `/debug-ui`
- Operations Dashboard แบบ lightweight ที่ `/` พร้อม read-only API ที่ `/api/dashboard`

## Project tree

```text
erp-ai-agent/
├── app.py                         # FastAPI API
├── run_server.py                  # python run_server.py
├── run_mock_data.py               # python run_mock_data.py
├── run_tests.py                   # python run_tests.py
├── ask.py                         # ถามและพิมพ์คำตอบใน terminal
├── requirements.txt
├── .env.example
├── agent/
│   ├── orchestrator.py            # bounded workflow + repair loop
│   ├── planner.py
│   ├── prompts.py
│   ├── result_analyzer.py
│   └── validator.py
├── config/settings.py
├── debug/trace_store.py
├── llm/openrouter_client.py
├── metadata/                      # 1 collection = 1 YAML
│   ├── customers.yaml
│   ├── maintenance.yaml
│   ├── payments.yaml
│   ├── promotions.yaml
│   ├── rentals.yaml
│   └── vehicles.yaml
├── models/
├── static/debug.html
├── tools/
│   ├── metadata_tool.py
│   └── mongodb_tool.py
└── tests/
```

## Installation

ต้องมี Python 3.11+ และ MongoDB user ที่มีสิทธิ์ read-only เท่านั้น

```powershell
cd "C:\Users\ANURAK\Desktop\Project ส่วนตัว\erp-ai-agent"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

แก้ `.env` โดยใส่ `OPENROUTER_API_KEY`, `MONGODB_URI` และ `MONGODB_DATABASE` ห้าม commit ไฟล์นี้ และควรสร้าง MongoDB role ที่อนุญาตเฉพาะ `find` สำหรับ collection ERP ที่ต้องใช้

### Environment variables

| ตัวแปร | จำเป็นเมื่อ | รายละเอียด |
|---|---|---|
| `OPENROUTER_API_KEY` | เรียก `POST /chat` | API key สำหรับให้ LLM วิเคราะห์ intent, plan และคำตอบ |
| `OPENROUTER_MODEL` | มีค่า default แล้ว | ค่าเริ่มต้นคือ `deepseek/deepseek-v4-flash` |
| `MONGODB_URI` | ถามข้อมูล ERP จริง | URI ของ MongoDB user แบบ read-only |
| `MONGODB_DATABASE` | ถามข้อมูล ERP จริง | ชื่อฐานข้อมูล ERP |
| `MONGODB_SEED_URI` | ไม่บังคับ | URI สำหรับ seed โดยเฉพาะ; เว้นว่างเพื่อใช้ `MONGODB_URI` |
| `MAX_AGENT_RETRY` | ไม่บังคับ | จำนวนครั้งที่ซ่อมและ retry query; default `3` |
| `MAX_QUERY_LIMIT` | ไม่บังคับ | จำนวนผลลัพธ์สูงสุด; default `100` |
| `MONGO_TIMEOUT_MS` | ไม่บังคับ | MongoDB timeout; default `5000` |
| `DEBUG_AGENT` | ไม่บังคับ | เปิด debug response/trace; default `true` |
| `DEBUG_LEVEL` | ไม่บังคับ | `basic` หรือ `full`; default `full` |
| `PRINT_ANSWER_TO_CONSOLE` | ไม่บังคับ | พิมพ์คำถามและคำตอบที่จัดรูปแบบแล้วในหน้าต่าง Server; default `true` |
| `TRACE_DIR` | ไม่บังคับ | ที่เก็บ trace; default `.debug_traces` |

การรัน `python run_tests.py` ไม่ต้องมี `.env`, OpenRouter key หรือ MongoDB เพราะ tests ใช้ fake LLM และ fake database ส่วน `GET /health`, Swagger และหน้า Debug UI เปิดได้โดยยังไม่ตั้ง credential แต่ `POST /chat` จะต้องใช้ `OPENROUTER_API_KEY` และคำถามที่อ่าน ERP จะต้องมี MongoDB configuration ครบ

### Mock data

สร้าง mock rentals 48 รายการ โดยวันเริ่มเช่ากระจายในช่วง 5 วันล่าสุดและแต่ละรายการมีระยะเช่า 3–5 วัน พร้อม customers, vehicles, payments, promotions และ maintenance ที่เชื่อมกัน:

```powershell
python run_mock_data.py
```

ปรับจำนวนรายการเช่าและช่วงวันได้:

```powershell
python run_mock_data.py --rentals 45 --days 4
```

Script ใช้ `MONGODB_SEED_URI` ถ้ามี มิฉะนั้นใช้ `MONGODB_URI` และเขียนเฉพาะ database จาก `MONGODB_DATABASE` ด้วย deterministic `_id` ผ่าน upsert จึงรันซ้ำได้โดยไม่เพิ่มข้อมูล mock ชุดเดิม ไม่ลบ document เดิมและไม่ drop collection ใด ๆ

เริ่ม API:

```powershell
python run_server.py
```

เปิด Operations Dashboard ที่ `http://127.0.0.1:8000/`, Swagger ที่
`http://127.0.0.1:8000/docs` และ Debug UI ที่ `http://127.0.0.1:8000/debug-ui`

ถามผ่าน terminal โดยไม่ต้องอ่าน JSON ใน Postman:

```powershell
python ask.py "รถทะเบียน กข1234 ตอนนี้ใครเช่าอยู่"
```

คำตอบจะแสดง newline จริงใน terminal ส่วนหน้าต่างที่รัน Server จะแสดงทั้งคำถาม คำตอบ และ `request_id` เมื่อ `PRINT_ANSWER_TO_CONSOLE=true`

## API examples

```powershell
curl.exe -X POST "http://127.0.0.1:8000/chat" `
  -H "Content-Type: application/json" `
  -d '{"message":"ลูกค้าคนนี้ทำไมได้วันเช่าเพิ่ม 7 วัน"}'
```

```powershell
curl.exe "http://127.0.0.1:8000/debug/req_REPLACE_ME"
```

```powershell
curl.exe -X POST "http://127.0.0.1:8000/debug/req_REPLACE_ME/feedback" `
  -H "Content-Type: application/json" `
  -d '{"feedback_type":"wrong_business_rule","comment":"ควรใช้กฎชดเชย"}'
```

ตัวอย่าง production response เมื่อปิด debug:

```json
{"success": true, "answer": "ลูกค้าได้รับวันเช่าเพิ่ม 7 วัน เนื่องจากมีการชดเชยจากปัญหาการใช้งาน"}
```

เมื่อ `DEBUG_AGENT=true` response จะมี `request_id`, metadata candidates, selected fields/relationships/rules, plan, query validation, executed query, retry history, result validation และ timeline

## Metadata

คู่มือเพิ่ม collection, aliases, business rules และ checklist ทดสอบอยู่ที่
[`METADATA_GUIDE.md`](METADATA_GUIDE.md)

เพิ่ม collection ด้วย YAML หนึ่งไฟล์ต่อ collection และต้องมี keys ต่อไปนี้ครบ:

```yaml
collection: rentals
description: เก็บข้อมูลรายการเช่ารถของลูกค้า
fields:
  adjustment_days:
    type: int
    description: จำนวนวันที่เพิ่มหรือลดจากสิทธิ์
  adjustment_reason:
    type: string
    description: สาเหตุโดยตรงที่ทำให้เกิดการเพิ่มหรือลดวัน
relationships: []
business_rules:
  - หาก adjustment_days ไม่เท่ากับ 0 ให้ตรวจ adjustment_reason
notes:
  - ห้ามเดาข้อมูลที่ metadata ไม่ได้ระบุ
```

`MetadataCatalog` เป็น interface แยกจาก workflow จึงเปลี่ยน keyword scoring เป็น BM25, embedding หรือ hybrid retrieval ภายหลังได้

## OpenRouter

Client เรียก `POST /api/v1/chat/completions`, ใช้ `response_format.type=json_schema`, `strict=true` และ `provider.require_parameters=true` ตาม [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs) Model ID ถูกตรวจจาก [OpenRouter models API](https://openrouter.ai/api/v1/models) และยังคง configurable เพื่อเปลี่ยน provider/model โดยไม่แก้ agent workflow

## Tests

```powershell
python run_tests.py
```

Tests ไม่ต้องใช้ OpenRouter key หรือ MongoDB จริง เพราะ inject fake LLM/database ผ่าน interface เดียวกับ production ครอบคลุม metadata loading/search, validator allow/block cases, read operations ทั้งห้า, ObjectId/datetime, limit, database error, full agent flow, debug fields และ secret redaction

## Security notes

- ตั้ง MongoDB user เป็น read-only ที่ database level ด้วย; application guard ไม่ใช่สิ่งทดแทน database authorization
- ห้ามเพิ่ม secret ลง YAML, prompt, trace หรือ log
- `execute_mongo_query()` validate ซ้ำเสมอ แม้ caller จะเรียก validator มาแล้ว
- trace ถูกเก็บใน `.debug_traces` แยกจาก ERP และเหมาะสำหรับ development; production ควรเพิ่ม authentication, retention, encryption และ access control
- `/`, `/api/dashboard`, `/debug`, `/debug-ui` และ raw result ต้องมี authentication/RBAC
  ก่อนเปิดใช้งานนอกเครือข่ายภายในใน production
- Prototype ยังไม่มี conversation memory, RBAC รายผู้ใช้, audit backend หรือ PII masking ระดับ production

## End-to-end example

```text
Question
  ลูกค้าคนนี้ทำไมได้วันเช่าเพิ่ม 7 วัน
Intent
  find_rental_adjustment_reason
Metadata
  rentals.adjustment_days + rentals.adjustment_reason + adjustment business rule
Query
  findOne (validated, read-only)
ERP result
  {adjustment_days: 7, adjustment_reason: "ชดเชยจากปัญหาการใช้งาน"}
Answer
  ลูกค้าได้รับวันเช่าเพิ่ม 7 วัน เนื่องจากมีการชดเชยจากปัญหาการใช้งาน
```

ถ้า `adjustment_reason` เป็น `null` ระบบจะตอบว่าข้อมูลสาเหตุไม่เพียงพอ ไม่สร้างเหตุผลขึ้นเอง
