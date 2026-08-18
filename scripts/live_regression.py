"""Concurrent live regression runner for the running /chat API.

This intentionally exercises the real configured LLM and MongoDB. Debug traces remain the
source of detailed evidence; terminal output is a compact batch summary.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx


QUESTIONS = [
    # Customers, payments, rentals, vehicles
    "ขอรายชื่อลูกค้าที่ยังมียอดต้องชำระ พร้อมเบอร์โทร ยอดคงเหลือรวม และจำนวนรายการที่ยังไม่ชำระครับ",
    "ลูกค้า CUS-0004 มีเบอร์อะไร และมีรายการ pending partial หรือ overdue เท่าไร รวมยอดค้างเท่าไรครับ",
    "สรุปตามรุ่นรถว่าเช่ากี่ครั้ง ยอดเรียกเก็บ ชำระแล้ว และคงเหลือเท่าไร โดยห้ามนับ payment ซ้ำ",
    "รถที่กำลังถูกเช่าอยู่ตอนนี้มีคันไหน ใครเช่า เบอร์อะไร เริ่มเช่าและกำหนดคืนเมื่อไรครับ",
    "รายการเช่าที่มีโปรโมชันมีอะไรบ้าง ขอชื่อลูกค้า รถ ทะเบียน ชื่อโปรโมชัน วันเพิ่ม และสถานะชำระเงิน",
    "ลูกค้าคนใดมียอด overdue มากที่สุด ขอชื่อ เบอร์ จำนวนรายการ ยอดรวม และ due date ที่เก่าที่สุด",
    # Maintenance and time
    "ช่วง 48 ชั่วโมงล่าสุดมีรถเข้าซ่อมอะไรบ้าง ขอทะเบียน รุ่น เวลาเปิดงาน รายละเอียด และสถานะ",
    "รถที่มีงานซ่อมยังไม่เสร็จมีคันไหน ขอทะเบียน รุ่น จำนวนงาน และรายละเอียดล่าสุด",
    "สรุปตามรุ่นรถว่ามีงานซ่อมทั้งหมดกี่ครั้ง งานที่ยังไม่เสร็จกี่ครั้ง และซ่อมเสร็จกี่ครั้ง",
    "มีลูกค้าที่ค้างชำระและรถในรายการเช่ามีงานซ่อมใน 3 วันที่ผ่านมาหรือไม่ ขอข้อมูลที่เกี่ยวข้อง",
    "รถสถานะ maintenance แต่ไม่มีงานซ่อม open หรือ in_progress มีหรือไม่ครับ",
    "งานซ่อมที่เสร็จแล้วใช้เวลากี่ชั่วโมงโดยเฉลี่ย แยกตามรุ่นรถครับ",
    # Incidents
    "ช่วง 3 วันที่ผ่านมามีอุบัติเหตุอะไรบ้าง ขอรหัสเหตุ ทะเบียน ลูกค้า severity status และความรับผิดลูกค้า",
    "สรุปอุบัติเหตุตามระดับความรุนแรง ขอจำนวนเหตุ estimated cost และ customer liability รวม",
    "ลูกค้าคนไหนมีอุบัติเหตุมากกว่าหนึ่งครั้ง ขอชื่อ เบอร์ จำนวนเหตุ และยอดรับผิดรวม",
    "อุบัติเหตุที่ยังไม่ resolved มีอะไรบ้าง ขอข้อมูลลูกค้า รถ รายละเอียด สถานะ และค่าเสียหายประมาณการ",
    "รายการเช่าที่มีทั้งอุบัติเหตุและยอดค้างชำระมีอะไรบ้าง โดยนับแต่ละ payment และ incident เพียงครั้งเดียว",
    "รุ่นรถใดมี customer liability จากอุบัติเหตุสูงสุด พร้อมจำนวนเหตุและค่าเฉลี่ยต่อเหตุ",
    # Charging
    "สรุปการชาร์จที่ completed ตามสถานี ขอจำนวนครั้ง พลังงานรวม ค่าใช้จ่ายรวม และราคาเฉลี่ยต่อ kWh",
    "ลูกค้าคนไหนชาร์จรถสำเร็จมากที่สุด ขอชื่อ จำนวนครั้ง พลังงานรวม และค่าใช้จ่ายรวม",
    "แต่ละรุ่นรถชาร์จสำเร็จกี่ครั้ง พลังงานรวมเท่าไร และค่าไฟเฉลี่ยต่อครั้งเท่าไร",
    "มี charging session ที่ failed อะไรบ้าง ขอทะเบียน ลูกค้า สถานี และตรวจว่าพลังงานกับค่าใช้จ่ายเป็นศูนย์หรือไม่",
    "รายการเช่าใดมีค่า charge รวมสูงสุด ขอชื่อลูกค้า ทะเบียน จำนวนครั้ง พลังงาน และค่าใช้จ่าย",
    "เปรียบเทียบค่า charging กับยอดเรียกเก็บตามรุ่นรถ โดยไม่ให้ fan-out ทำยอดซ้ำ",
    # Installments
    "ลูกค้าคนไหนมีแผนผ่อน defaulted ขอรหัสแผน ชื่อ เบอร์ ทะเบียน งวด overdue และยอดคงเหลือ",
    "สรุปแต่ละแผนผ่อนว่าชำระแล้วกี่บาท ยังเหลือกี่บาท มีกี่งวด pending partial overdue และ paid",
    "งวดที่ครบกำหนดภายใน 7 วันข้างหน้ามีอะไรบ้าง ขอรหัสแผน ลูกค้า ยอดงวด ยอดชำระ และ due date",
    "ลูกค้าคนไหนมียอดคงเหลือจากตารางผ่อนสูงสุด ขอชื่อ รหัสแผน จำนวนงวด และยอดคงเหลือจริง",
    "เปรียบเทียบ total_amount ของแผนกับยอดจากทุก schedule ว่ามีแผนไหนไม่ตรงกันหรือไม่",
    "แผนผ่อนที่ active แต่มีงวด overdue มีหรือไม่ครับ อย่าสรุปจาก plan status อย่างเดียว",
    # Campaigns
    "สรุป campaign ทุกตัว ขอ unique customers, conversion, attributed revenue, budget และ ROI เรียงสูงสุด",
    "campaign ไหนมี conversion rate สูงสุด โดยระบุ numerator denominator และจำนวนลูกค้าไม่ซ้ำ",
    "ลูกค้าที่ opted_out จาก campaign มีใครบ้าง ขอชื่อ เบอร์ แคมเปญ และวันที่ติดต่อ",
    "รายการเช่าที่เกิดจาก campaign conversion มีอะไรบ้าง ขอ campaign ลูกค้า รถ ทะเบียน และ attributed revenue",
    "เปรียบเทียบ campaign กับ promotion ว่ารายการเช่าที่ conversion แล้วใช้ promotion ด้วยมีอะไรบ้าง",
    "ช่องทางการตลาดแต่ละช่องทางใช้งบรวม รายได้รวม conversion รวม และ ROI เท่าไร",
    # Cross-domain / fan-out
    "สรุปตามรุ่นรถ: จำนวนเช่า ยอดค้าง งานซ่อมไม่เสร็จ อุบัติเหตุ และค่าชาร์จ โดยแต่ละ entity ต้องนับครั้งเดียว",
    "ลูกค้าที่มีทั้งยอดค้าง แผนผ่อนค้าง และอุบัติเหตุมีใครบ้าง ขอผลรวมแต่ละประเภทโดยไม่ซ้ำ",
    "รถคันใดมีทั้งงานซ่อม อุบัติเหตุ และ charging session ขอทะเบียน รุ่น และจำนวนแต่ละรายการแบบ distinct",
    "รายการเช่าที่เกี่ยวข้องกับ campaign promotion payment charging และ incident พร้อมกันมีหรือไม่ ขอรายละเอียด",
    "จัดอันดับลูกค้าตามยอดที่ต้องรับผิดรวมจาก payment ค่างวด และอุบัติเหตุ โดยแยกยอดแต่ละแหล่งก่อนรวม",
    "สรุปต้นทุนและรายได้ตามรุ่นรถจาก payment, charging cost และ incident estimated cost โดยไม่คูณซ้ำจาก join",
    # Alias, empty, and wording robustness
    "ขอ customer_info ของ CUS-0001 พร้อม rental_info และ payment ที่ยัง due",
    "damage_report ที่ status investigating มีของ customers คนไหนและ vehicles อะไรบ้าง",
    "ขอ charge_session ของ car รุ่น Good Cat ที่สำเร็จ พร้อม energy_usage รวม",
    "ขอ payment_plan และ installment_due ของลูกค้าที่ผิดนัดทั้งหมด",
    "มีข้อมูลลูกค้ารหัส CUS-9999 หรือไม่ ถ้าไม่พบอย่าเดาว่าไม่มีหนี้จาก query ที่กรองหนี้อย่างเดียว",
    "ช่วง 2 วันนี้มีอุบัติเหตุและงานซ่อมอะไรบ้าง ตีความเป็น rolling 48 ชั่วโมงและบอกช่วงเวลาให้ตรง query",
    # Focused semantic stress cases discovered during live optimization
    "สำหรับ CUS-0004 ขอเฉพาะยอดของคนนี้เท่านั้น: เบอร์ จำนวน payment ที่ค้าง และผลรวม amount-paid_amount",
    "ตรวจทุก installment plan แล้วบอกเฉพาะแผนที่ total_amount ไม่เท่ากับผลรวม schedule; ถ้าไม่มีให้บอกว่าทุกแผนตรง",
    "รุ่นรถไหนมี liability รวมสูงสุด ต้องรวมรถทุกคันใน model เดียวกัน ไม่ใช่เลือก vehicle คันเดียว",
    "ตาม model รถ ขอ paid_amount รวม ค่า charge เฉพาะ completed และ estimated incident cost โดย pre-aggregate แต่ละกิ่ง",
    "หาลูกค้าที่ไม่มี payment ค้างเลย แต่มี incident โดยต้องคงลูกค้าที่ child array ว่างไว้ด้วย",
    "หารถที่ไม่มี charging completed แต่มี rental อย่างน้อยหนึ่งครั้ง ขอทะเบียน รุ่น และจำนวนเช่า",
    "เมื่อวานตามวันปฏิทินไทยมี maintenance เปิดกี่งาน ขอช่วง 00:00 ถึง 23:59:59 ตาม Asia/Bangkok",
    "72 ชั่วโมงล่าสุดมี incident กี่เหตุ ขอเวลาเริ่มและเวลาสิ้นสุดของ rolling window ให้ตรง query",
    "งวด overdue ของ PLAN-0002 มีกี่งวด ค้างเท่าไร และ due date อะไร ห้ามรวมแผนอื่น",
    "ลูกค้า customer_info CUS-0001 ไม่มีหรือมียอดค้างเท่าไร โดยต้องแยกกรณีไม่พบลูกค้ากับพบแต่ไม่มีหนี้",
    "campaign แต่ละตัวมี converted customer ไม่ซ้ำกี่คน ห้ามนับ null จาก outcome อื่น",
    "คำนวณ conversion rate ของ campaign เป็น converted engagements หาร engagements ทั้งหมด พร้อมแสดงทั้งสองจำนวน",
    "campaign ที่ไม่มี conversion มีหรือไม่ ถ้าไม่มีอย่าบอกว่าไม่มีข้อมูล campaign",
    "charging failed มีกี่ session และ energy/cost ทุกแถวเป็นศูนย์จริงหรือไม่",
    "ค่า charging completed รวมทุก model ต้องรวมได้เท่าไร และจำนวน session completed ทั้งหมดกี่ครั้ง",
    "customer liability รวมทุก incident เท่าไร แยก minor major critical และยอดรวมทั้งหมด",
    "รถแต่ละ model มีจำนวน vehicle ไม่ซ้ำ rental ไม่ซ้ำ และ incident ไม่ซ้ำเท่าไร ห้ามนับจากแถว fan-out",
    "ลูกค้าแต่ละคนมี rental, payment, charging และ incident กี่รายการแบบ distinct แสดง 5 อันดับแรก",
    "สรุปยอด payment ค้างตาม model พร้อมจำนวน rental ที่เกี่ยวข้อง โดย payment หนึ่งรายการต้องถูกนับครั้งเดียว",
    "เปรียบเทียบรายได้ paid_amount กับ charging completed cost และ incident cost ตาม model พร้อมกำไรสุทธิแบบง่าย",
    "รายการเช่าไหนไม่มี payment document เลย ใช้ left join และอย่าปนกับ payment ที่ paid แล้ว",
    "รถ maintenance ทุกคันต้องมี maintenance open/in_progress หรือไม่ แสดงเฉพาะคันที่ผิดเงื่อนไข",
    "แผน active ทุกแผนไม่มี overdue จริงหรือไม่ ตรวจจาก schedules ไม่ใช่เดาจาก plan status",
    "ขอรายการ incident ที่ resolved แต่ resolved_at ว่าง หรือยังไม่ resolved แต่ resolved_at มีค่า ถ้าไม่มีให้บอกว่าข้อมูลสอดคล้อง",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


async def ask(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, index: int, question: str) -> dict[str, Any]:
    async with semaphore:
        try:
            response = await client.post("/chat", json={"message": question})
            payload = response.json()
            debug = payload.get("debug") or {}
            execution = debug.get("execution") or {}
            return {
                "index": index,
                "http": response.status_code,
                "success": payload.get("success", False),
                "request_id": debug.get("request_id") or (payload.get("detail") or {}).get("request_id"),
                "status": debug.get("status") or (payload.get("detail") or {}).get("error_type"),
                "rows": execution.get("row_count"),
                "retries": max(0, len(debug.get("retry_history") or []) - 1),
                "ms": round(debug.get("total_execution_ms") or 0, 1),
                "answer": str(payload.get("answer") or "")[:180].replace("\n", " "),
            }
        except Exception as exc:
            return {"index": index, "success": False, "error": f"{type(exc).__name__}: {exc}"}


async def run(args: argparse.Namespace) -> int:
    selected = list(enumerate(QUESTIONS[args.start:args.start + args.count], start=args.start))
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(base_url=args.url.rstrip("/"), timeout=180.0) as client:
        results = await asyncio.gather(*[
            ask(client, semaphore, index, question) for index, question in selected
        ])
    visible_results = (
        [item for item in results if not item.get("success") or item.get("retries", 0) > 0]
        if args.compact else results
    )
    print(json.dumps({
        "round": args.round,
        "start": args.start,
        "results": visible_results,
        "hidden_clean_results": len(results) - len(visible_results),
    }, ensure_ascii=False, indent=2))
    failed = sum(not item.get("success", False) for item in results)
    print(json.dumps({
        "summary": {
            "count": len(results),
            "failed": failed,
            "retried": sum(item.get("retries", 0) > 0 for item in results),
            "average_ms": round(sum(item.get("ms", 0) for item in results) / max(1, len(results)), 1),
        }
    }, ensure_ascii=False))
    return 1 if failed else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    if args.start < 0 or args.count < 1 or args.concurrency < 1:
        raise SystemExit("start must be >= 0; count and concurrency must be >= 1")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
