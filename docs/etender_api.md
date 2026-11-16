# etender.uzex.uz — ichki JSON API (o'rganildi: 16.11.2026)

Sayt Angular SPA, ma'lumotlarni `https://apietender.uzex.uz` dan oladi. Endpointlar
`main.*.js` bundle'idan topildi va haqiqiy so'rovlar bilan tekshirildi.
**Quyidagi 3 ta endpoint uchun login/token kerak emas.**

Sarlavhalar: oddiy brauzer `User-Agent`, `Origin: https://etender.uzex.uz`,
`Content-Type: application/json` (POST uchun). So'rovlar orasida ≥ 2 s pauza.

---

## 1. Ochiq lotlar ro'yxati

`POST https://apietender.uzex.uz/api/common/TradeList`

```json
{"From": 1, "To": 200, "System_Id": 0}
```

- Paging: `From`/`To` (1 dan boshlanadi, ikkala chegara ham kiradi). `To-From = 199` (200 qator) ishladi, javob ~0.25 s.
- Javob: massiv. Har qatorda `total_count` bor (16.11.2026 da 620 ta ochiq lot) → bir tekshiruvga ~4 so'rov ketadi.
- Tartib: `end_date` bo'yicha o'sish (muddati yaqin lotlar oldinda).
- Ixtiyoriy filtr maydonlari (bundle'da bor): `Keyword`, `RegionId`, `DistrictId`, `CategoryId`,
  `PriceMin`, `PriceMax`, `CustomerTin`, `TypeId`, `DeadlineStart`, `DeadlineEnd`, `CurrencyId`, `ProductCode`.
  ⚠️ `Keyword` ishonchsiz: "дастурий" so'ziga 1 ta aloqasiz lot qaytardi. **Shuning uchun hammasini olib,
  kalit so'z filtrini o'zimizda qo'llaymiz.**

Qator maydonlari:

| maydon | misol | izoh |
|---|---|---|
| `id` | 524034 | lot raqami, havola: `https://etender.uzex.uz/lot/{id}` |
| `display_no` | "26110012524034" | to'liq raqam |
| `name` | "Навбахор туман…" | predmet (kirill yoki lotin) |
| `start_date` / `end_date` | "2026-11-17T08:46:30" | Toshkent vaqti, TZ belgisisiz |
| `clarific_date` | | ko'p hollarda `end_date` ga teng, savol muddati sifatida ishonchsiz |
| `cost` | 337279766.0 | boshlang'ich narx |
| `currency_codeabc` | "UZS" | |
| `seller_name`, `seller_tin` | | buyurtmachi va INN |
| `region_name`, `district_name` | | viloyat va tuman |
| `type_id` | 1 / 2 | 1 = "Eng yaxshi takliflarni tanlash", 2 = "Tender" |

## 2. Lot kartasi

`GET https://apietender.uzex.uz/api/common/GetTrade/{id}/0`  (~30 KB)

Spetsifikatsiyaning 4-bo'limidagi maydonlar quyidagilarga mos keladi:

| bizga kerak | API maydoni | izoh |
|---|---|---|
| buyurtmachi, INN, viloyat | `customer_name`, `customer_tin`, `customer_region_name` | |
| predmet | `technical_description` / `addon_description`, `budget_products[].Product_Name` | |
| boshlang'ich narx | `start_cost` + `currency_codeabc` | `proposal_converted_costs` da USD/EUR ekvivalenti ham bor |
| zakalat | `pledge_value` (%), `pledge_name`, `is_bank_guarantee` | summa = `start_cost × pledge_value / 100` |
| avans % | `advance_payment_perc` | |
| to'lov muddati | `term_payment_days`, `payment_type_name` | |
| ish muddati | `budget_products[].Delivery_Term` | kunlarda bo'lishi taxmin qilinmoqda |
| kafolat muddati | `budget_products[].Guarantee_Term` + `Term_Period_Name` | masalan 3 "Месяцев", 1 "Год (лет)" |
| e'lon / topshirish | `start_date` / `end_date` | Asia/Tashkent |
| baholash usuli | `valuation_name` ("Ball usuli"), `tech_coef`, `cost_coef` | masalan 70 / 30 |
| o'tish bali | `min_point` | |
| texnik mezonlar va ballar | `js_fields[]`: `label`, `max_score`, `gravity`, `description`, `is_required` | |
| malaka talablari | `js_qualification_fields[].name` | |
| taklif tili | `languages` (JSON satr) | `[{"Id":1,"Name":"Русский"},{"Id":2,"Name":"Узбекский"}]` |
| moliyalash manbai | `financing_source` | null bo'lishi mumkin |
| holat | `status_id`, `status_name` ("Eʼlon qilindi" = 4) | |
| muddat uzaytirildi | `is_prolonged`, `prolong_date`, `prolonged_days`, `prolong_reason` | "🔄 O'zgarish" xabari uchun |
| fayllar | `tech_file_path` (TZ), `tech_doc_file_path`, `add_file_path`, `contract_proform_file_path`, `expertise_file_path` | + `*_file_name`, `*_file_ext`, `*_file_sizes` |

`budget_products`, `languages`, `contacts`, `fields`, `qualification_fields` JSON **satr** sifatida keladi
(ikkinchi marta `json.loads` qilish kerak). `js_fields` va `js_qualification_fields` esa tayyor massiv.

## 3. Fayl yuklash (TZ, shartnoma loyihasi)

`POST https://apietender.uzex.uz/api/common/DownloadFile?path={file_path}`  (tanasi bo'sh)

- `path` boshidagi `/` bor yoki yo'qligidan qat'i nazar ishlaydi. Javob: `application/pdf` (yoki docx).
- Tekshirildi: 220 KB va 1.09 MB PDF'lar yuklandi.
- `https://apietender.uzex.uz/files/…` ga to'g'ridan-to'g'ri GET → 404, ishlatilmaydi.

## Boshqa topilgan, lekin hozir kerak emas

- `GET /api/common/GetProposalUsers/{id}`: ishtirokchilar (yopilgandan keyin)
- `GET /api/Common/GetCriteriaFiles/{id}`, `GET /api/Common/GetDataForCalcByLot/{id}`: zakalat kalkulyatori uchun
- `GET /api/common/GetTopStats/0`, `GET /api/common/GetMapStats`: statistika
- Boshqa xostlar: `https://api-etender-new.uzex.uz`, `https://etender-fileservice.uzex.uz`,
  `https://xarid-api-trade.uzex.uz` (xarid uchun, 4-bosqichda o'rganiladi)

## Birinchi sinov: oddiy regex bilan 200 lotdan topilganlari

To'g'ri topildi: 524100 (kiber tahdidlar dasturiy ta'minoti), 523525 (CRM uchun TZ yozish, 1.7 mlrd),
523549 (Xalq banki), 524795 (hisobot-monitoring tizimi).
Noto'g'ri topildi: "Muzrabot" (`bot` so'z o'rtasida), "mezzanin **platforma**", "jihozlar" (uskuna).
→ Filtrda so'z chegarasi va istisnolar ro'yxati shart. Bu misollar testlarga qo'shildi (`tests/fixtures/etender/`).
