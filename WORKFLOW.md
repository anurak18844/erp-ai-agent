# Workflow และ Logic ของ ERP AI Agent

เอกสารนี้อธิบาย flow ตาม implementation ปัจจุบัน ตั้งแต่รับคำถาม อ่าน metadata YAML ในเครื่อง ส่งข้อมูลที่จำเป็นให้ DeepSeek ผ่าน OpenRouter สร้างและตรวจ MongoDB query ไปจนถึงสร้างคำตอบสุดท้าย

ไฟล์หลักที่เกี่ยวข้อง:

| หน้าที่ | ไฟล์ |
|---|---|
| FastAPI endpoint | `app.py` |
| ควบคุม workflow | `agent/orchestrator.py` |
| วิเคราะห์ intent และสร้าง query plan | `agent/planner.py` |
| System/phase prompts | `agent/prompts.py` |
| อ่านและค้น metadata | `tools/metadata_tool.py` |
| ตรวจ MongoDB query | `agent/validator.py` |
| Execute MongoDB | `tools/mongodb_tool.py` |
| ตรวจและสรุปผล | `agent/result_analyzer.py` |
| เรียก OpenRouter | `llm/openrouter_client.py` |
| เก็บ debug trace | `debug/trace_store.py` |

## ภาพรวม

```text
User
  │
  │ POST /chat {"message": "..."}
  ▼
FastAPI
  │
  ▼
ERPAgentOrchestrator
  │
  ├─ 1. Python อ่านเฉพาะรายชื่อ collection แล้ว DeepSeek วิเคราะห์ Intent/Domain
  │
  ├─ 2. Python อ่านและค้น metadata YAML ในเครื่อง
  │
  ├─ 3. Python ส่งเฉพาะ metadata ที่เลือกให้ DeepSeek
  │
  ├─ 4. DeepSeek คืน Logical Plan + Mongo Query Spec
  │
  ├─ 5. Python ตรวจ query กับ metadata
  │       └─ ไม่ผ่าน → DeepSeek Repair → ตรวจใหม่
  │
  ├─ 6. Python Execute MongoDB
  │       └─ Error → DeepSeek Repair → Retry สูงสุด 3 ครั้ง
  │
  ├─ 7. Python ตรวจว่าผลลัพธ์เพียงพอหรือไม่
  │
  ├─ 8. DeepSeek สร้างคำตอบจากผลจริง
  │
  └─ 9. Python เก็บ Debug Trace และส่ง response
```

DeepSeek เป็น API ภายนอก จึงไม่สามารถเปิดไฟล์ YAML หรือเชื่อม MongoDB เองได้ Python เป็นผู้ทำงานทั้งสองอย่าง แล้วส่งข้อมูลในรูป JSON ให้ model วิเคราะห์

## Intent/Domain ถูกวิเคราะห์ตอนไหน

Intent/Domain เป็น **AI Call แรก** เกิดทันทีหลัง FastAPI รับและ validate คำถาม โดย Python
อ่านเฉพาะชื่อ physical collection จาก metadata catalog แล้วส่งรายชื่อให้ DeepSeek เป็นขอบเขต
ในการเลือก domain รอบนี้ยังไม่ส่ง description, fields, relationships, business rules หรือ notes
เพื่อสร้าง query และยังไม่แตะ MongoDB

ลำดับเวลาที่เกิดขึ้นจริง:

| ลำดับ | ผู้ทำงาน | การทำงาน | เรียก AI หรือไม่ |
|---:|---|---|---|
| 1 | FastAPI | รับ `message` และตรวจความยาว | ไม่เรียก |
| 2 | Python + DeepSeek | ส่งรายชื่อ collection จริง แล้ววิเคราะห์ Intent/Primary Domain/Secondary Domains จากรายชื่อนั้น | **AI Call 1** |
| 3 | Python | นำคำถาม + Intent + Domains ไปค้น YAML ในเครื่อง | ไม่เรียก |
| 4 | DeepSeek | อ่าน metadata candidates แล้วเลือก collection/field และสร้าง Query Plan/Query Spec | **AI Call 2** |
| 5 | Python | Validate และ execute MongoDB | ไม่เรียก |
| 6 | DeepSeek | วิเคราะห์ MongoDB result และสร้างคำตอบ | **AI Call 3** |

ตำแหน่งในโค้ด:

```python
# agent/orchestrator.py
intent = await self.planner.analyze_intent(           # AI Call 1
    question,
    sorted(self.catalog.collections),
)

metadata_query = " ".join([
    question,
    intent.intent,
    intent.primary_domain,
    *intent.secondary_domains,
])
search = self.catalog.search(metadata_query)          # Local YAML search

planned = await self.planner.create_plan(             # AI Call 2
    question,
    intent,
    search.metadata_context,
)
```

ข้อมูลที่ส่งใน Intent Call มี System Prompt, Intent Prompt, คำถาม และรายชื่อ collection จริง
แบบสั้น โดยยังไม่ส่งเนื้อหา metadata ภายใน collection:

```json
{
  "model": "deepseek/deepseek-v4-flash",
  "messages": [
    {
      "role": "system",
      "content": "You are an ERP data assistant... Never assume schema..."
    },
    {
      "role": "user",
      "content": "Classify... Input: {\"question\":\"ลูกค้าคนนี้ทำไมได้วันเช่าเพิ่ม 7 วัน\",\"available_collections\":[\"customers\",\"payments\",\"promotions\",\"rentals\",\"vehicles\"]}"
    }
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "intentanalysis",
      "strict": true
    }
  }
}
```

AI ต้องคืนโครงสร้างนี้:

```json
{
  "intent": "find_rental_adjustment_reason",
  "primary_domain": "rentals",
  "secondary_domains": ["promotions", "customers"],
  "needs_database": true
}
```

จากนั้น Python จึงใช้ผลนี้ช่วยค้น YAML:

```text
คำถามเดิม
+ find_rental_adjustment_reason
+ rentals
+ promotions
+ customers
→ MetadataCatalog.search(...)
```

ดังนั้น Intent/Domain ไม่ได้เกิดจาก keyword hardcode ใน Python แต่เป็น structured output จาก DeepSeek
ภายใต้รายชื่อ collection ที่มีจริง ส่วน Python ยังนำผลนั้นไปประกอบ local metadata retrieval
เหมือนเดิม รายชื่อดังกล่าวเป็นเพียงขอบเขตชื่อ ไม่ใช่การเลือก metadata หรือ relationship ล่วงหน้า

## ขอบเขตความรับผิดชอบ

```text
DeepSeek / OpenRouter
  - วิเคราะห์คำถาม
  - สร้าง intent
  - สร้าง logical plan
  - เสนอ Mongo Query Spec
  - ซ่อม query เมื่อมี error
  - สรุปคำตอบจากข้อมูลที่ได้รับ

Python Application
  - อ่านไฟล์ YAML
  - เลือก metadata
  - ตรวจ schema และ relationship
  - บังคับ read-only query
  - อ่าน credential จาก .env
  - เชื่อม MongoDB
  - Execute query
  - จำกัดจำนวนผลลัพธ์
  - กรอง secret ใน trace
```

## ขั้นที่ 1: รับคำถามที่ FastAPI

Request:

```http
POST /chat
Content-Type: application/json

{
  "message": "ลูกค้าคนนี้ทำไมได้วันเช่าเพิ่ม 7 วัน"
}
```

`app.py` ใช้ Pydantic ตรวจว่า `message`:

- ไม่เป็นค่าว่าง
- ยาวไม่เกิน 4,000 ตัวอักษร

จากนั้นเรียก:

```python
answer, trace = await orchestrator.run(request.message)
```

## ขั้นที่ 2: Intent/Domain Analysis

`ERPAgentOrchestrator` เรียก:

```python
intent = await planner.analyze_intent(
    question,
    sorted(catalog.collections),
)
```

รอบนี้ยังไม่ส่ง metadata ฉบับเต็มและไม่ส่ง MongoDB credential โดยส่งเพียงคำถามกับ
`available_collections` ซึ่งเป็นรายชื่อ physical collection:

```json
[
  {
    "role": "system",
    "content": "System prompt: ห้ามเดา schema, relationship, business rule หรือผลลัพธ์"
  },
  {
    "role": "user",
    "content": "Classify intent/domain... Input: {question: ..., available_collections: [customers, payments, promotions, rentals, vehicles]}"
  }
]
```

OpenRouter ส่ง request ไปยัง model:

```text
deepseek/deepseek-v4-flash
```

Model ต้องตอบตาม JSON Schema ของ `IntentAnalysis`:

```json
{
  "intent": "find_rental_adjustment_reason",
  "primary_domain": "rentals",
  "secondary_domains": ["promotions", "customers"],
  "needs_database": true
}
```

ถ้า `needs_database=false` เช่นคำทักทาย ระบบสามารถตอบผ่าน LLM โดยไม่ค้น metadata หรือ MongoDB

## ขั้นที่ 3: Python อ่าน Metadata YAML

ไฟล์ metadata อยู่ใน:

```text
metadata/
├── customers.yaml
├── maintenance.yaml
├── payments.yaml
├── promotions.yaml
├── rentals.yaml
└── vehicles.yaml
```

Python อ่านไฟล์ด้วย PyYAML ใน `MetadataCatalog.load()`:

```python
for path in sorted(metadata_dir.glob("*.yaml")):
    with path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
```

หลังอ่านแล้ว YAML จะกลายเป็น Python dictionary ใน memory:

```python
{
    "rentals": {
        "collection": "rentals",
        "description": "เก็บข้อมูลรายการเช่ารถของลูกค้า",
        "fields": {
            "adjustment_days": {
                "type": "int",
                "description": "จำนวนวันที่เพิ่มหรือลดจากสิทธิ์"
            },
            "adjustment_reason": {
                "type": "string",
                "description": "สาเหตุโดยตรงที่ทำให้เกิดการเพิ่มหรือลดวัน"
            }
        },
        "relationships": [],
        "business_rules": [
            "หาก adjustment_days ไม่เท่ากับ 0 ให้ใช้ adjustment_reason เป็นสาเหตุหลัก"
        ]
    }
}
```

ทุก YAML ต้องมี keys เหล่านี้ครบ:

```text
collection
description
fields
relationships
business_rules
notes
```

หากไฟล์ขาด key หรือมีชื่อ collection ซ้ำ `MetadataCatalog` จะหยุดด้วย error แทนการใช้ schema ที่ไม่สมบูรณ์

## ขั้นที่ 4: ค้น Metadata ในเครื่อง

Orchestrator รวมข้อความสำหรับค้นจาก:

```python
metadata_query = " ".join([
    question,
    intent.intent,
    intent.primary_domain,
    *intent.secondary_domains,
])
```

ตัวอย่าง:

```text
ลูกค้าคนนี้ทำไมได้วันเช่าเพิ่ม 7 วัน
find_rental_adjustment_reason
rental
promotion
customer
```

`MetadataCatalog.search()` ให้คะแนนจากคำที่ตรงกับ:

- ชื่อ collection และ description
- ชื่อ field และ description
- business rules
- relationships

ตัวอย่างผล local search:

```json
{
  "candidates": [
    {
      "collection": "rentals",
      "score": 1.0,
      "selected": true,
      "reason": "Matched collection description, field adjustment_days, field adjustment_reason, business rule"
    },
    {
      "collection": "promotions",
      "score": 0.54,
      "selected": true,
      "reason": "Matched collection description and promotion fields"
    },
    {
      "collection": "maintenance",
      "score": 0.05,
      "selected": false,
      "reason": "No relevant metadata terms matched"
    }
  ]
}
```

ขั้นตอนนี้ทำในเครื่องทั้งหมด ยังไม่มีการเรียก OpenRouter เพิ่ม และไม่มีข้อมูล ERP จริง

V1 เลือก metadata ที่มีคะแนนมากกว่า 0 สูงสุด 4 collections แล้วเก็บเป็น `metadata_context`

## ขั้นที่ 5: ส่ง Metadata ให้ DeepSeek

Python ไม่ส่ง path ให้ model เปิดเอง เช่นไม่ส่งแค่ `metadata/rentals.yaml` เพราะ model เปิดไฟล์ local ไม่ได้

Python ส่งเนื้อหาที่อ่านและเลือกแล้วเป็น JSON string:

```python
payload = {
    "question": question,
    "intent": intent.model_dump(),
    "metadata": metadata_context,
}

content = json.dumps(payload, ensure_ascii=False)
```

ตัวอย่างข้อมูลที่ถูกส่งใน Planning Call:

```json
{
  "question": "ลูกค้าคนนี้ทำไมได้วันเช่าเพิ่ม 7 วัน",
  "intent": {
    "intent": "find_rental_adjustment_reason",
    "primary_domain": "rental",
    "secondary_domains": ["promotion", "customer"],
    "needs_database": true
  },
  "metadata": {
    "rentals": {
      "collection": "rentals",
      "fields": {
        "customer_id": {
          "type": "ObjectId",
          "description": "รหัสลูกค้า"
        },
        "adjustment_days": {
          "type": "int",
          "description": "จำนวนวันที่เพิ่มหรือลดจากสิทธิ์"
        },
        "adjustment_reason": {
          "type": "string",
          "description": "สาเหตุโดยตรงที่ทำให้เกิดการเพิ่มหรือลดวัน"
        }
      },
      "business_rules": [
        "หาก adjustment_days ไม่เท่ากับ 0 ให้ใช้ adjustment_reason เป็นสาเหตุหลัก"
      ]
    }
  }
}
```

หมายเหตุ: implementation ปัจจุบันส่ง document metadata เต็มของ collection ที่ถูกเลือก ไม่ได้ส่งทุก collection และไม่ได้ให้ model อ่าน filesystem

## ขั้นที่ 6: OpenRouter Structured Output

`OpenRouterClient.generate_structured()` เรียก:

```text
POST https://openrouter.ai/api/v1/chat/completions
```

พร้อมค่าประมาณนี้:

```json
{
  "model": "deepseek/deepseek-v4-flash",
  "messages": [],
  "temperature": 0,
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "plannedquery",
      "strict": true,
      "schema": "Pydantic JSON Schema ของ PlannedQuery"
    }
  },
  "provider": {
    "require_parameters": true
  }
}
```

`OPENROUTER_API_KEY` อยู่ใน HTTP Authorization header ไม่ได้อยู่ใน prompt content

DeepSeek ต้องตอบ `PlannedQuery` ซึ่งประกอบด้วย:

```json
{
  "plan": {
    "goal": "ค้นสาเหตุที่ลูกค้าได้รับวันเช่าเพิ่ม 7 วัน",
    "collections": ["rentals"],
    "required_fields": [
      "rentals.customer_id",
      "rentals.adjustment_days",
      "rentals.adjustment_reason"
    ],
    "steps": [
      "ระบุรายการเช่าของลูกค้า",
      "อ่าน adjustment_days",
      "อ่าน adjustment_reason",
      "ใช้ business rule อธิบายผล"
    ]
  },
  "query": {
    "operation": "findOne",
    "collection": "rentals",
    "filter": {
      "adjustment_days": 7
    },
    "projection": {
      "adjustment_days": 1,
      "adjustment_reason": 1
    }
  }
}
```

Model ไม่สามารถส่ง raw Mongo shell เช่น:

```javascript
db.rentals.find(...)
```

เพราะ application รับเฉพาะ Pydantic `MongoQuerySpec`

## ขั้นที่ 7: Query Validation

ก่อน execute Python ตรวจ query ด้วย `QueryValidator`:

```text
operation อนุญาตหรือไม่
collection มีใน metadata หรือไม่
collection ถูก retrieve ในรอบนี้หรือไม่
field มีจริงหรือไม่
ชนิดข้อมูลถูกต้องหรือไม่
relationship ของ $lookup มีประกาศหรือไม่
aggregate stage ปลอดภัยหรือไม่
limit เกิน MAX_QUERY_LIMIT หรือไม่
มี write operation หรือ JavaScript operator หรือไม่
```

Operations ที่อนุญาต:

```text
find
findOne
aggregate
count
distinct
```

ตัวอย่างที่ถูก block:

```text
insert / update / delete
$where / $function
$out / $merge
unknown collection
unknown field
undeclared $lookup relationship
```

ถ้า model แต่ง field:

```json
{
  "projection": {
    "rental_reason": 1
  }
}
```

Validator จะคืน:

```json
{
  "valid": false,
  "errors": ["Unknown field: rental_reason"]
}
```

MongoDB จะยังไม่ถูกเรียก

## ขั้นที่ 8: Repair Loop

เมื่อ validation หรือ MongoDB execution ล้มเหลว Python ส่งเฉพาะ context ที่จำเป็นให้ DeepSeek:

```json
{
  "original_question": "...",
  "query_plan": {},
  "query_spec": {},
  "metadata_context": {},
  "error": "Unknown field: rental_reason",
  "attempt": 1
}
```

Model คืน:

```json
{
  "error_cause": "Field is not declared in metadata",
  "repair_summary": "Use adjustment_reason from rentals metadata",
  "query": {}
}
```

Python ตรวจ query ใหม่ทุกครั้ง และหยุดทันทีถ้า query ที่ซ่อมเหมือน query เดิม

จำนวน retry สูงสุดมาจาก:

```env
MAX_AGENT_RETRY=3
```

## ขั้นที่ 9: Execute MongoDB

เมื่อ query ผ่าน validation `MongoQueryExecutor` จะ:

1. Validate ซ้ำ
2. อ่าน `MONGODB_URI` และ `MONGODB_DATABASE` ภายใน application
3. เลือก collection ตาม query spec
4. แปลง string เป็น ObjectId/datetime ตาม metadata
5. Execute operation ที่อนุญาต
6. จำกัดจำนวน rows
7. แปลง BSON/ObjectId/datetime เป็น JSON-safe values

ตัวอย่างผล:

```json
{
  "success": true,
  "row_count": 1,
  "data": [
    {
      "adjustment_days": 7,
      "adjustment_reason": "ชดเชยจากปัญหาการใช้งานระบบล็อกรถ"
    }
  ],
  "execution_ms": 18.4
}
```

DeepSeek ไม่ได้รับ `MONGODB_URI`, username หรือ password

## ขั้นที่ 10: Result Validation

Python ตรวจผลก่อนให้ model สรุป เช่น:

```text
query สำเร็จหรือไม่
พบ record หรือไม่
คำถามถามหาเหตุผล แต่ adjustment_reason ว่างหรือไม่
```

ตัวอย่างข้อมูลไม่พอ:

```json
{
  "adjustment_days": 7,
  "adjustment_reason": null
}
```

ผล validation:

```json
{
  "sufficient": false,
  "reason": "The direct reason field is empty"
}
```

ขั้นนี้ป้องกัน model สร้างเหตุผลเองเมื่อฐานข้อมูลไม่มีข้อมูล

## ขั้นที่ 11: ส่งผลให้ DeepSeek สร้างคำตอบ

Final Answer Call ส่ง:

```json
{
  "question": "ลูกค้าคนนี้ทำไมได้วันเช่าเพิ่ม 7 วัน",
  "intent": {},
  "result": {
    "success": true,
    "row_count": 1,
    "data": [
      {
        "adjustment_days": 7,
        "adjustment_reason": "ชดเชยจากปัญหาการใช้งานระบบล็อกรถ"
      }
    ]
  },
  "result_validation": {
    "sufficient": true,
    "reason": "Returned fields can answer the detected intent"
  },
  "business_rules": [
    "หาก adjustment_days ไม่เท่ากับ 0 ให้ใช้ adjustment_reason เป็นสาเหตุหลัก"
  ]
}
```

DeepSeek ตอบ:

```text
ลูกค้าได้รับวันเช่าเพิ่ม 7 วัน เนื่องจากมีการชดเชยจากปัญหาการใช้งานระบบล็อกรถ
```

ถ้าข้อมูลไม่พอ model ถูกสั่งให้ตอบว่าข้อมูลไม่เพียงพอแทนการคาดเดา

### รูปแบบภาษาของคำตอบ

Final Answer Prompt กำหนดให้ตอบเหมือนผู้ช่วยบริการ ไม่ใช่หน้าจอฐานข้อมูล:

```text
หลีกเลี่ยง:
รถสถานะ available

ควรตอบ:
ขณะนี้รถคันนี้พร้อมให้เช่าครับ
```

```text
หลีกเลี่ยง:
รถสถานะ rented

ควรตอบเมื่อมีเฉพาะสถานะ:
ขออภัยครับ ขณะนี้รถคันนี้กำลังมีผู้เช่าอยู่ จึงยังไม่ว่างในตอนนี้

ควรตอบเมื่อมีวันคืนด้วย:
ขออภัยครับ ขณะนี้รถคันนี้กำลังมีผู้เช่าอยู่ และมีกำหนดคืนวันที่ 21 สิงหาคมครับ
```

```text
หลีกเลี่ยง:
payment status = partial

ควรตอบ:
รายการนี้ชำระแล้วบางส่วน 2,250 บาท จากยอด 4,500 บาท จึงเหลือยอดชำระอีก 2,250 บาทครับ
```

Model สามารถเสริมข้อมูลที่ช่วยตัดสินใจได้หนึ่งหรือสองประเด็น เช่น วันคืน เหตุผลที่ไม่ว่าง ยอดคงเหลือ วันครบกำหนด หรือสิทธิ์โปรโมชั่น แต่ทุกข้อมูลต้องอยู่ใน MongoDB result หรือ business rule ที่ได้รับเท่านั้น

คำว่า `available` หมายถึงพร้อมให้เช่า ณ เวลาที่ตรวจสอบ ไม่ได้ยืนยันว่ารถว่างตลอดช่วงวันที่ผู้ใช้ต้องการ หากไม่มีข้อมูลการจองครอบคลุมช่วงนั้นต้องแจ้งข้อจำกัดอย่างสุภาพ

## ขั้นที่ 12: Debug Trace

เมื่อเปิด:

```env
DEBUG_AGENT=true
DEBUG_LEVEL=full
```

ระบบเก็บ:

```text
request_id
question
intent/domains
metadata search query
candidate collections และคะแนน
selected fields/relationships/business rules
logical query plan
query validation
executed Mongo query
execution result และเวลา
retry/repair history
result validation
final answer
Why This Query?
```

Trace ถูก redact ก่อนเขียนไฟล์ `.debug_traces` โดยกรองชื่อ key และข้อความที่มีลักษณะ:

```text
API key
Authorization header
MongoDB URI
username/password/credential
```

## จำนวนครั้งที่เรียก LLM ต่อหนึ่งคำถาม

กรณี database query สำเร็จครั้งแรก:

```text
Call 1: Intent/Domain Analysis
Call 2: Logical Plan + Mongo Query Spec
Call 3: Final Answer
```

ถ้า query มีปัญหา จะมี Repair Call เพิ่มหนึ่งครั้งต่อ attempt แต่ไม่เกิน `MAX_AGENT_RETRY`

Metadata search, query validation, MongoDB execution และ result validation ทำโดย Python ไม่เสีย LLM call

## ข้อมูลอะไรถูกส่งออกไป OpenRouter

ส่งออก:

- คำถามของผู้ใช้
- System/phase prompt
- รายชื่อ physical collection ทั้งหมดใน AI Call 1 โดยไม่รวม description หรือ business rules
- intent/domain
- metadata ของ collection ที่ถูกเลือก
- structured query plan/query spec
- MongoDB result ที่ query คืนมา
- error ที่จำเป็นสำหรับ repair
- business rules ที่เกี่ยวข้อง

ไม่ส่งออก:

- `OPENROUTER_API_KEY` ใน prompt
- `MONGODB_URI`
- database username/password
- ไฟล์ `.env`
- path หรือไฟล์อื่นในเครื่องที่ไม่ได้ถูกอ่านโดย application
- raw Mongo shell access

ข้อควรพิจารณา: MongoDB result อาจมีข้อมูลลูกค้าตาม projection ที่ query เลือก ดังนั้น production ควรเพิ่ม authentication, role-based field filtering และ PII masking ก่อนส่งผลให้ LLM

## สรุป Logic

```text
LLM ไม่อ่าน YAML โดยตรง
→ Python อ่าน YAML และเลือกส่วนที่เกี่ยวข้อง
→ Python แปลงเป็น JSON แล้วส่งให้ LLM

LLM ไม่เชื่อม MongoDB
→ LLM เสนอ Structured Query Spec
→ Python ตรวจ query กับ YAML
→ Python execute MongoDB

Query สำเร็จไม่ได้แปลว่าคำตอบเพียงพอ
→ Python ตรวจ result
→ LLM สรุปเฉพาะข้อมูลจริงที่ได้รับ
```
