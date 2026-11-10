# Giti Priority Tire — Tuesday MAP Monitor

This repository scrapes **Priority Tire only**, for these four Giti patterns:

- GitiComfort XA1
- WeatherSmart
- GitiXCursion XT
- GitiXCursion AT

The scraper discovers all product variants from the model seed's `__NEXT_DATA__` / Apollo state, visits each size, and extracts price, part number and stock status. It then matches model and normalized size to the **155 user-supplied 2026 MAP prices** in `scraper/map_prices_2026.csv`.

## Schedule

The GitHub Actions workflow `.github/workflows/tuesday_priority_giti.yml` runs **every Tuesday at 10:00 UTC** (5:00 AM EST or 6:00 AM EDT). It can also be started in GitHub Actions using **Run workflow**. GitHub-hosted scheduled jobs may start late.

## Run locally

```bash
pip install -r requirements.txt
playwright install chromium
python scraper/priority_giti_scraper.py
python scraper/standardize_giti_prices.py
```

Files (saved in repository working directory):

- `priority_giti_raw_YYYY-MM-DD.csv`: all scraped variants, including unsuccessful/OOS rows
- `tire_prices_YYYY-MM-DD.csv`: raw variants joined with MAP, `below_map`, `map_difference`
- `priority_checkpoint.csv`: partial data checkpoint every 25 variants

Download results under the workflow run's **Artifacts** section. GitHub Actions does not automatically commit these results into the repository.

`below_map` is true when the observed per-tire selling price is lower than the provided MAP. Missing MAP remains blank and is reported in logs. Stock, pricing, and variant discovery depend on the retailer's live markup and may require maintenance if their website changes.

Note: 35x12.5R22 in the provided MAP list is preserved as a flotation size; it will be matched using normalized 35X12.50R22. For other flotation sizes `LT` suffix is retained.
