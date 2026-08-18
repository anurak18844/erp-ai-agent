# คู่มือเพิ่ม Collection ด้วย Metadata

แนวทางหลักของโปรเจกต์คือให้แก้ YAML ก่อนแก้ Python เมื่อเพิ่มโดเมนธุรกิจหรือคำเรียกใหม่
หนึ่ง collection ใช้หนึ่งไฟล์ใน `metadata/` และระบบค้นพบไฟล์ใหม่อัตโนมัติเมื่อ restart/reload

## รูปแบบขั้นต่ำ

```yaml
collection: physical_collection_name
aliases: [ชื่อเอกพจน์, ชื่อพหูพจน์, ชื่อที่พนักงานใช้, english_alias]
search_terms: [คำค้นเพิ่มเติมที่ไม่มีอยู่ในคำอธิบาย]
description: อธิบายว่า collection นี้เก็บ entity อะไร
fields:
  _id: {type: ObjectId, description: รหัสภายใน, required: true}
  status: {type: string, description: สถานะ, enum: [active, completed]}
relationships:
  - target_collection: another_collection
    local_field: another_id
    foreign_field: _id
    relationship_type: many-to-one
    description: อธิบายความสัมพันธ์ทางธุรกิจ
business_rules:
  - เขียนความหมาย enum สูตรคำนวณ และเงื่อนไขการนับเป็นภาษาธรรมดา
notes:
  - ระบุ grain เช่น หนึ่ง document คือหนึ่งเหตุการณ์ และวิธีป้องกันการนับซ้ำหลัง join
```

`collection` และชื่อใน `fields` ต้องตรงกับชื่อจริงใน MongoDB ส่วน `aliases` และ
`search_terms` เพิ่มได้อย่างอิสระ เช่น `customer`, `customers`, `customer_info`, `ลูกค้า`
โดยไม่เปลี่ยน physical schema ชื่อที่ AI สร้างใน `$lookup.as`, `$project`, `$group` หรือ
`$addFields` ก็เป็น query-local alias และไม่จำเป็นต้องประกาศเป็น field จริง

## สิ่งที่ควรเขียนใน business_rules/notes

- ความหมายของทุก status ที่มีผลต่อการตัดสินใจ
- สูตร เช่น `outstanding = amount - paid_amount`
- ฐานการนับ: document, ลูกค้าไม่ซ้ำ, รายการเช่าไม่ซ้ำ หรือรถไม่ซ้ำ
- กฎ fan-out: หาก join child สองกิ่ง ให้สรุปแต่ละกิ่งก่อนนำมารวม
- ขอบเขตเวลาและ field วันที่ที่เป็น source of truth
- ข้อยกเว้น เช่น cancelled/void ไม่รวมในยอด
- ข้อมูลที่ห้ามเปิดเผยเกินคำถาม

## Checklist หลังเพิ่มไฟล์

```powershell
python run_tests.py
python run_mock_data.py
python run_audit.py
```

จากนั้นถามอย่างน้อยสามระดับ: collection เดียว, join 2–3 hop และ aggregate ที่มี
one-to-many สองกิ่ง ตรวจ `request_id` ใน `.debug_traces/` และเทียบผลกับ `run_audit.py`
อย่าตัดสินจากข้อความตอบเพียงอย่างเดียว

ตัวอย่างครบดูได้จาก `metadata/campaign_engagements.yaml`,
`metadata/installment_schedules.yaml` และ `metadata/incidents.yaml`
