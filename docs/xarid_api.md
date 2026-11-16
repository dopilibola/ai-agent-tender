# xarid.uzex.uz va xt-xarid.uz — o'rganildi 17.11.2026

## xarid.uzex.uz — «Taklif so'rovi» (ishlatilmoqda)
Angular SPA. Backend xostlari bundle'dan topildi: `xarid-api-prequest` (taklif so'rovi), `-trade`,
`-auction`, `-auctionx`, `-purchase`, `-shop`, `-common` (`.uzex.uz`). Ochiq (login'siz) endpointlar:

| Vazifa | So'rov |
|---|---|
| Ochiq lotlar | `POST https://xarid-api-prequest.uzex.uz/api/Public/GetLots` `{"from":1,"to":300}` → `{"Status":200,"Data":[…]}` |
| Lot kartasi | `GET …/api/Public/GetLot?id={id}` |
| Lot fayli | `GET …/api/Files/GetLotFileById?id={files[].id}` (groupId=1) |
| Sahifa | `https://xarid.uzex.uz/request-proposal/3/detail/{id}` |

- 17.11.2026 da 1 108 ta ochiq lot; `totalCount` har qatorda. 300 tadan sahifa → 4 so'rov.
- Ro'yxatda **mahsulot nomi yo'q**, faqat `categoryName` → kalit so'z filtri avval kategoriyaga
  qo'llanadi, karta faqat IT'ga o'xshash kategoriyalar uchun ochiladi (1 108 dan ~5–27 ta).
- Kartada: `details[]` (productName, quantity, price, cost, properties), `advanceCost`, `advanceTerm`,
  `decreeName`, `files[]`, `statusId` (2 = «Опубликован»). Zakalat yo'q, baholash — eng past narx.
- Vaqtlar Toshkent vaqti, TZ belgisisiz.
- Auksion va e-do'kon — tovarlar savdosi, IT xizmatlar uchun qo'shilmadi.

## xt-xarid.uz — ulanmadi
- Vue SPA, ma'lumot faqat `api.xt-xarid.uz/api/…` va `/rpc/…` orqali keladi, `robots.txt` esa
  aynan `/api/` va `/rpc/` ni **taqiqlaydi**.
- Ochiq sitemap bor (`https://api.xt-xarid.uz/sitemap/ad.xml`, ~3 100 lot havolasi), lekin unda faqat
  ID bor — nomi/narxi yo'q, IT'ni ajratib bo'lmaydi.
- Bir necha so'rovdan keyin sayt (bosh sahifa ham) javob bermay qo'ydi (timeout) — so'rovlar to'xtatildi.
- Yo'llar: operatordan rasmiy API/ruxsat so'rash yoki saytdagi email obunasini Gmail kuzatuvi orqali o'qish.
