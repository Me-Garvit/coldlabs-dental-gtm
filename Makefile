PY := ./.venv/bin/python

.PHONY: all sweep webmd merge probe qualify enrich prep reviews final qa budget clean

all: sweep webmd merge probe qualify enrich prep reviews final

sweep:   ; $(PY) -u scripts/maps_sweep.py --per-zip 30 --batch-cap-usd 1.00
webmd:   ; $(PY) -u scripts/webmd_scraper.py --max-pages 45
merge:   ; $(PY) -u scripts/merge_dedupe.py
probe:   ; $(PY) -u scripts/site_probe.py
qualify: ; $(PY) -u scripts/qualify.py
enrich:  ; $(PY) -u scripts/clean_enrich.py
prep:    ; $(PY) -u scripts/final_list.py prep
reviews: ; $(PY) -u scripts/dental_reviews.py --in data/reviews_input.csv --out data/reviews_output.csv
final:   ; $(PY) -u scripts/final_list.py join && $(PY) -u scripts/validate_output.py
qa:      ; $(PY) -u scripts/validate_output.py
budget:  ; $(PY) scripts/providers.py

clean:   ; rm -rf data/*.csv data/*.json data/*.md
