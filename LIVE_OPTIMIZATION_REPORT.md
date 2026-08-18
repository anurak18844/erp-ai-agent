# Live optimization report

วันที่ทดสอบ: 17 สิงหาคม 2026  
ฐานข้อมูล: `ev_rental_erp`

## ขอบเขตการทดสอบ

- ส่งคำถามจริงผ่าน agent 281 ครั้ง จากชุดคำถาม 72 รูปแบบ และวนซ้ำหลายรอบเพื่อตรวจความไม่แน่นอนของโมเดล
- ครอบคลุม query แบบ collection เดียว, aggregation, relative date, empty-result semantics และ join 2–5 collections
- ผลเชิงเทคนิคสำเร็จ 276/281 ครั้ง (98.22%) ระหว่างกระบวนการ optimize
- พบรูปแบบความผิดพลาดเชิงความหมาย 7 กลุ่มและแก้พร้อมเพิ่ม regression tests
- OpenRouter usage สุดท้าย `$0.484591057` จาก limit `$1.00`

## สิ่งที่ปรับปรุง

1. ผ่อน schema validation ให้รองรับ alias และฟิลด์ที่เกิดขึ้นระหว่าง aggregation แต่ยังคุม read-only และ relationship ที่เสี่ยง
2. รองรับ nested lookup output schema และ correlated lookup ที่ปลอดภัย
3. ป้องกัน lookup ที่ไม่ผูกกับเอกสารต้นทาง ซึ่งเคยทำให้รวม payment ของลูกค้าทุกคน
4. แยกความหมาย relationship ตาม grain เช่น payment → rental → vehicle แทนการเชื่อมจาก customer อย่างเดียว
5. ตรวจจับ fan-out จากการ unwind หลาย one-to-many branches ก่อน aggregate
6. เพิ่มกติกา group by model, conditional distinct count, payment/charging status และ empty-result semantics
7. บังคับใช้ runtime clock สำหรับวันที่สัมพัทธ์ และหลีกเลี่ยง date operators ที่ executor V1 ไม่รองรับ
8. เพิ่ม metadata retrieval จาก 5 เป็น 6 collections และเพิ่มคะแนน collection ที่ผู้ใช้ระบุโดยตรง
9. เพิ่ม operators ที่ปลอดภัย เช่น `$map`, `$reduce`, `$anyElementTrue`, `$allElementsTrue`, `$setDifference`
10. เพิ่มกติกา absence query: ต้อง lookup child ทุกสถานะก่อนตรวจ array ว่าง

## ข้อมูลธุรกิจที่เพิ่ม

- `incidents`: บันทึกอุบัติเหตุและความรับผิดชอบค่าเสียหาย
- `charging_sessions`: ประวัติชาร์จ พลังงาน และต้นทุน
- `installment_plans` และ `installment_schedules`: แผนผ่อนและงวดชำระ
- `campaigns` และ `campaign_engagements`: แคมเปญ งบประมาณ การตอบรับ และ conversion

ฐานข้อมูลมีทั้งหมด 12 collections, 240 documents และผ่าน independent audit

## ผลตรวจสุดท้าย

- Automated tests: 57/57 passed
- Database audit: passed
- จำนวนรถที่มี active rental: 14
- จำนวนรถที่มีงานซ่อมไม่เสร็จ: 3
- Campaign conversions: 8

## ข้อจำกัดที่ยังควรติดตาม

- LLM ยังมีความไม่แน่นอน จึงควรเก็บ semantic regression questions และตรวจผลกับ oracle/audit ต่อเนื่อง
- Executor V1 ยังไม่รองรับ MongoDB expressions บางตัวและ pipeline-form `$lookup`; prompt จึงต้องสร้าง query จาก capability ที่ประกาศไว้
- Technical success ไม่เท่ากับ semantic correctness เสมอ ควรตรวจ grain, fan-out, status scope และความหมายของผลลัพธ์ว่างทุกครั้งที่เพิ่ม use case ใหม่
