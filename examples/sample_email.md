# Sample email — written using only columns from the final list

No research, no custom writing. Every fact below is a cell in `final_list.csv`.

**Lead row:** `Complete Dental Care` · `completedentalcaretampa.com` ·
Tampa, FL 33612 · rating `3.4` on `23` reviews · `owner_replied_to_bad: 0 of 5` ·
competitor 1 `North Pointe Dental Of North Tampa` `5.0` / `1276` ·
`rating_gap: +1.6` · `doctors: Dr. Kimberly L Hannah Jones`

---

**Subject:** 3.4 vs 5.0 in 33612

Hi Dr. Hannah Jones,

Complete Dental Care sits at **3.4 stars across 23 Google reviews**. North Pointe
Dental, in the same ZIP, is at **5.0 across 1,276** — patients comparing you side
by side see a 1.6-star gap before they ever call.

The five most recent 1-star reviews aren't about clinical work:

- Talisha G. — bad root canal, wrong teeth
- Amy M. — waited 45 minutes for callback
- Ashla B. — door locked past opening time
- Kiandra G. — booked appointments with no availability

None of the five has a reply from the practice.

That last part is the opening. Replying to negative reviews and asking happy
patients at checkout typically moves a practice with your review count fastest,
because 23 reviews is a small enough base that 30 new ones reset the average.

Worth a 15-minute call to walk through what North Pointe is doing differently?

— [Sender]

---

### Which column did each claim come from

| claim | column |
|---|---|
| "3.4 stars across 23 Google reviews" | `google_rating`, `review_count` |
| "North Pointe Dental … 5.0 across 1,276" | `competitor_1_name/_rating/_reviews` |
| "1.6-star gap" | `rating_gap` |
| the four complaint bullets | `bad_reviews` |
| "None of the five has a reply" | `owner_replied_to_bad` (`0 of 5`) |
| "Dr. Hannah Jones" | `doctors` |
| "33612" / "same ZIP" | `zip`, and the strict same-ZIP competitor rule |

The only sentence not derived from a column is the advice paragraph, which is the
offer, not a claim about the prospect.
