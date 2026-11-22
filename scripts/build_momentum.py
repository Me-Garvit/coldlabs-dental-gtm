"""Assemble the momentum segment (good rating, recent bad reviews) into a CSV."""
import collections, csv, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import competitors as comp, normalize as nz

ROOT = pathlib.Path(__file__).resolve().parent.parent

def i(v):
    try: return int(float(v or 0))
    except (TypeError, ValueError): return 0

def main():
    enr = {r['domain']: r for r in csv.DictReader(open(ROOT/'data/enriched.csv'))}
    summ = json.loads((ROOT/'data/momentum_summaries.json').read_text())
    pool = collections.defaultdict(list)
    for r in csv.DictReader(open(ROOT/'data/raw_maps.csv')):
        z = nz.norm_zip(r['zip'])
        if z: pool[z].append(r)
    prior = comp.pool_mean([p for z in pool.values() for p in z])

    rows = []
    for r in csv.DictReader(open(ROOT/'data/recent_decline.csv')):
        rows.append((r, i(r['bad_in_window']), i(r['bad_in_window_with_text']),
                     r['owner_replied_in_window'], r['last_bad_review_date'],
                     i(r['reviews_in_window']), 'high_volume'))
    seen = {x[0]['domain'] for x in rows}
    for r in csv.DictReader(open(ROOT/'data/decline_output.csv')):
        if r['domain'] in seen: continue
        rows.append((r, i(r['bad_review_count_90d']), i(r['bad_review_count_90d']),
                     r['owner_replied_to_bad'], r['last_bad_review_date'], 0, 'low_volume'))

    out = []
    for r, bad90, badtxt, replied, lastbad, win, cohort in rows:
        e = enr.get(r['domain'])
        if not e: continue
        cands, _ = comp.find_competitors(e, pool, prior)
        row = {'company_name': e['company_name'], 'doctors': e.get('doctors',''),
               'domain': r['domain'],
               'momentum_flag': ("actively_slipping" if badtxt >= 2
                                 else "one_recent_complaint" if bad90 >= 1 else "quiet"),
               'google_rating': e['rating'], 'review_count': e['review_count'],
               'bad_last_90d': bad90, 'bad_last_90d_with_text': badtxt,
               'owner_replied_to_recent': replied, 'last_bad_review_date': lastbad,
               'bad_reviews': "\n".join(f"- {s}" for s in summ.get(r['domain'], [])),
               'bad_reviews_lifetime': i(e['reviews_1star']) + i(e['reviews_2star']),
               'reviews_last_90d': win or '', 'city': e['city'], 'state': e['state'],
               'zip': e['zip'], 'cohort': cohort, 'source': e.get('source',''),
               'specialty': e.get('specialty',''), 'phone': e.get('phone',''),
               'maps_url': e.get('maps_url','')}
        for n in (1, 2, 3):
            c = cands[n-1] if len(cands) >= n else None
            row[f'competitor_{n}_name'] = c['name'] if c else ''
            row[f'competitor_{n}_rating'] = c['rating'] if c else ''
            row[f'competitor_{n}_reviews'] = c['reviews'] if c else ''
        row['rating_gap'] = (f"+{cands[0]['rating']-float(e['rating']):.1f}"
                             if cands and e['rating'] else '')
        out.append(row)

    order = {'actively_slipping': 0, 'one_recent_complaint': 1, 'quiet': 2}
    out.sort(key=lambda r: (order[r['momentum_flag']], -r['bad_last_90d'],
                            -float(r['google_rating'] or 0)))
    outp = ROOT/'deliverables/momentum_list.csv'
    with outp.open('w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    c = collections.Counter(r['momentum_flag'] for r in out)
    print(f"wrote {outp} — {len(out)} practices; {dict(c)}")
    print(f"  with dentist names: {sum(1 for r in out if r['doctors'])}/{len(out)}")

if __name__ == '__main__':
    main()
