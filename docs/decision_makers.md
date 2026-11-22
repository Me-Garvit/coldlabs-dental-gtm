# Decision makers and email sourcing

*How we'd get from "this practice has a review problem" to "this named person
reads the email".*

---

## Who actually signs

In a 1–3 location independent practice the buying committee is two people:

| role | why they matter |
|---|---|
| **Owner dentist** | Signs. It is their name on the bad reviews, their PLLC on the lease, and in a practice this size no one else commits recurring spend. |
| **Office / practice manager** | Finds the tool, evaluates it, runs it, and in most practices holds a card with authority up to roughly $500/month. They are the champion, and often the only one who answers email during clinic hours. |

Target both. Owner as decision maker, manager as champion. A front-desk contact
is a last-resort route to a generic inbox, never the pitch target.

---

## Contact sources, in priority order

| # | source | what it gives | cost | expected hit rate |
|---|---|---|---|---|
| 1 | **Practice website** — "Meet the Doctor", "Our Team", footer | owner dentist name, manager name, sometimes direct emails | free (we already fetch every site in Stage 3a) | high — most practices name their dentist |
| 2 | **WebMD dentist names** | dentists at the practice, already attached in the `doctors` column | free, already collected | 52% of practices in this run |
| 3 | **State business filings** — FL Sunbiz (free), TX SOS | the legal officer/manager of the PLLC or PA, i.e. the actual owner | free (FL), small fee (TX) | high, and it is the *authoritative* owner |
| 4 | **Owner replies on Google reviews** | often signed "– Dr. Patel" or "Lisa, Office Manager" | free, already in the review payload | moderate; also proves they read reviews |
| 5 | **State dental board license lookup** | confirms a licensed dentist at that address | free | high for confirmation, low for discovery |
| 6 | **NPI Registry / LinkedIn / AI Ark / Prospeo** | fills the gaps | paid | low for practices this small |

The ordering is deliberate: **steps 1–4 are free and already in our data**. B2B
tools are the last resort, not the first, because they have poor coverage on
two-person dental practices.

### What this run already has

Stage 2 attached **1,515 dentist names to 370 practices** from WebMD, and Stage
3a already fetched every practice homepage — so sources 1, 2 and 4 are sitting
in the repo and need extraction, not acquisition.

---

## Email waterfall

1. **Scrape what is published.** `mailto:` links, contact page, footer, privacy
   policy. Small practices routinely publish `drsmith@` or even a personal
   Gmail/AOL address. This is the single highest-yield step and it is free.
2. **Pattern + verify.** Generate `drlastname@`, `firstname@`, `drfirstlast@`,
   `firstname.lastname@`, `flastname@` against the practice's root domain, then
   verify with MillionVerifier or Reoon. Only ship `valid`.
3. **B2B waterfall, in cost order.** Prospeo → AI Ark → Findymail / LeadMagic →
   Hunter. Hunter goes last-but-useful because it crawls the open web rather
   than LinkedIn, which is the right shape for a tiny domain with no company
   page.
4. **Generic inbox fallback.** `office@`, `info@`, `frontdesk@`, addressed to
   "Dr. {Lastname}". The office manager reads it, and in this segment that is
   often the better outcome anyway.

### Handling poor coverage honestly

- **Name first, email second.** Most coverage comes from steps 1–4, not from
  paid tools. A named owner with a generic inbox beats an unnamed `info@`.
- **Catch-all tiering.** Ship as `verified` / `catch-all + pattern` / `generic`.
  Catch-alls send from separate domains at lower volume so they cannot damage
  the main sending reputation.
- **Phone as a real channel.** Every Maps listing carries one. In this segment
  the front desk answers, and that is a legitimate second touch.
- **Pilot before scaling.** Run 10–20 practices through the full waterfall and
  report the true hit rate per step before committing to a volume number.

---

## Job title filter

### Include

**Owner / buyer**
`Owner` · `Practice Owner` · `Founder` · `Co-Founder` · `Principal Dentist` ·
`Managing Dentist` · `Lead Dentist` · `President` · `CEO` · `Managing Partner` ·
`Partner` (with DDS/DMD)

**Dentist titles** — only when the person matches the business filing owner or
is the sole dentist at the practice
`Dentist` · `DDS` · `DMD` · `General Dentist` · `Cosmetic Dentist` ·
`Orthodontist` / `Oral Surgeon` (specialty practices only)

**Champion**
`Office Manager` · `Practice Manager` · `Practice Administrator` ·
`Operations Manager` · `Business Manager` · `Marketing Manager/Director`
(larger independents only)

### Exclude

| title | why |
|---|---|
| `Associate Dentist`, `Associate` | employed, does not own or sign |
| `Hygienist`, `RDH`, `Dental Assistant`, `EFDA`, `Sterilization` | clinical staff, no budget |
| `Receptionist`, `Front Desk`, `Scheduling`, `Patient Coordinator`, `Treatment Coordinator`, `Billing`, `Insurance Coordinator` | no authority; generic-inbox route only |
| `Student`, `Resident`, `Intern`, `Faculty`, `Professor` | not at a buying practice |
| `Regional Manager`, `Area Director`, `VP Operations`, `Director of Practice Development` | **DSO titles — seeing one means a chain slipped through the filter and the row should be re-checked** |
| `Lab Technician`, `Sales Rep`, `Consultant`, `Agency` | vendors, not buyers |

That last row is doing double duty: the title filter is also a **QA signal on
the chain filter**. A DSO title appearing on a supposedly independent practice
means Stage 3's `dso_chain` rule missed one.
