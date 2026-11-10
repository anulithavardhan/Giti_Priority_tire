"""Generate the nine-column, in-stock-only Priority Tire final CSV."""
from pathlib import Path
from datetime import datetime, timezone
import re
import pandas as pd

BASE = Path(__file__).resolve().parent
DATE = datetime.now(timezone.utc).strftime('%Y-%m-%d')
FINAL_COLUMNS = [
    'model', 'size', 'price_per_tire', 'website', 'raw size',
    'date', 'MAP', 'url', 'part_number',
]


def normalize_size(value):
    if pd.isna(value):
        return None
    s = str(value).upper().strip().replace(' ', '')
    metric = re.search(r'(?<![A-Z0-9])(LT|P)?(\d{3})/(\d{2})Z?R(\d{2})', s)
    if metric:
        prefix = metric.group(1) or ''
        remaining = s[metric.end():]
        if prefix != 'LT' and re.match(r'[- ]?\d{3}[/\-]\d{3}[A-Z]', remaining):
            prefix = 'LT'
        if prefix == 'P':
            prefix = ''
        return f'{prefix}{metric.group(2)}/{metric.group(3)}R{metric.group(4)}'
    flotation = re.search(r'(\d{2,3})X(\d{1,2})\.(\d{1,2})R(\d{2})(LT)?', s)
    if flotation:
        diameter, width, decimal, rim, lt = flotation.groups()
        return f"{diameter}X{int(width)}.{decimal.ljust(2, '0')}R{rim}{lt or ''}"
    return None


def is_true(value):
    """Treat only explicit true stock values as in stock, never missing values."""
    return value is True or str(value).strip().lower() in ('true', '1')


def main():
    source = Path(f'priority_giti_raw_{DATE}.csv')
    if not source.exists():
        raise FileNotFoundError(f'Expected {source}; run scraper first')
    data = pd.read_csv(source)
    map_df = pd.read_csv(BASE / 'map_prices_2026.csv')

    # Exclude False, blank, unknown, and missing stock indicators.
    input_count = len(data)
    data = data.loc[data['in_stock'].map(is_true)].copy()
    print(f'Stock filter: {len(data)} in-stock rows retained out of {input_count} discovered.')

    data['model'] = data['model'].astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
    data['raw size'] = data['size'].apply(normalize_size)
    map_df['model'] = map_df['Pattern'].astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
    map_df['raw size'] = map_df['Size'].apply(normalize_size)
    if map_df.duplicated(['model', 'raw size']).any():
        raise ValueError('Duplicate model/size in 2026 MAP!')

    data['price_per_tire'] = pd.to_numeric(data['price_per_tire'], errors='coerce')
    map_df['MAP'] = pd.to_numeric(map_df['MAP 2026'], errors='coerce')
    result = data.merge(
        map_df[['model', 'raw size', 'MAP']],
        on=['model', 'raw size'], how='left', validate='many_to_one',
    )
    result['website'] = 'Priority Tire'
    result['date'] = DATE
    result['part_number'] = result['part_number'].fillna(result['sku'])

    # Keep exactly these nine columns and nothing else, in the requested order.
    result = result.reindex(columns=FINAL_COLUMNS)
    out = Path(f'tire_prices_{DATE}.csv')
    result.to_csv(out, index=False)
    missing_map = result['MAP'].isna().sum()
    print(f'Saved {out}: {len(result)} in-stock rows; {missing_map} missing MAP values.')
    if missing_map:
        print('Missing MAP matches:')
        print(result.loc[result['MAP'].isna(), ['model', 'size', 'raw size']].head(30).to_string(index=False))
    if result.empty:
        raise RuntimeError('No in-stock variants found; final CSV contains headers only.')


if __name__ == '__main__':
    main()
