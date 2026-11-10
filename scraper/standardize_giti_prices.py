"""Join Priority Tire prices to user-provided 2026 MAP values. Preserve missing matches."""
from pathlib import Path
from datetime import datetime, timezone
import re
import pandas as pd

BASE = Path(__file__).resolve().parent
DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")

def normalize_size(value):
    if pd.isna(value):
        return None
    s = str(value).upper().strip().replace(" ", "")
    # 245/75R16 120/116S or 245/75R16 120-116S: LT size inferred from dual load index
    metric = re.search(r"(?<![A-Z0-9])(LT|P)?(\d{3})/(\d{2})Z?R(\d{2})", s)
    if metric:
        prefix = metric.group(1) or ""
        remaining = s[metric.end():]
        if prefix != "LT" and re.match(r"[- ]?\d{3}[/\-]\d{3}[A-Z]", remaining):
            prefix = "LT"
        if prefix == "P":
            prefix = ""
        return f"{prefix}{metric.group(2)}/{metric.group(3)}R{metric.group(4)}"
    flotation = re.search(r"(\d{2,3})X(\d{1,2})\.(\d{1,2})R(\d{2})(LT)?", s)
    if flotation:
        diameter,width,decimal,rim,lt = flotation.groups()
        return f"{diameter}X{int(width)}.{decimal.ljust(2, '0')}R{rim}{lt or ''}"
    return None

def main():
    source = Path(f"priority_giti_raw_{DATE}.csv")
    if not source.exists():
        raise FileNotFoundError(f"Expected {source}; run scraper first")
    data = pd.read_csv(source)
    map_df = pd.read_csv(BASE / "map_prices_2026.csv")
    data['model'] = data['model'].astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
    data['RAW SIZE'] = data['size'].apply(normalize_size)
    map_df['model'] = map_df['Pattern'].astype(str).str.replace(r'\s+', ' ', regex=True).str.strip()
    map_df['RAW SIZE'] = map_df['Size'].apply(normalize_size)
    if map_df.duplicated(['model','RAW SIZE']).any():
        raise ValueError('Duplicate model/size in 2026 MAP!')
    data['price_per_tire'] = pd.to_numeric(data['price_per_tire'],errors='coerce')
    map_df['MAP'] = pd.to_numeric(map_df['MAP 2026'],errors='coerce')
    result = data.merge(map_df[['model','RAW SIZE','MAP']],on=['model','RAW SIZE'],how='left',validate='many_to_one')
    result['website'] = 'Priority'
    result['DATE'] = DATE
    result['below_map'] = (result['price_per_tire'] < result['MAP']).where(result['price_per_tire'].notna() & result['MAP'].notna())
    result['map_difference'] = (result['price_per_tire'] - result['MAP']).round(2)
    result['part_number'] = result['part_number'].fillna(result['sku'])
    # Keep every discovered variant (including OOS/errors) for audit; MAP missing is clearly visible.
    cols = ['model','size','price_per_tire','website','part_number','url','RAW SIZE','DATE','MAP','below_map','map_difference','in_stock','error','sku','rating','review_count','warranty']
    result=result.reindex(columns=cols)
    out=Path(f"tire_prices_{DATE}.csv")
    result.to_csv(out,index=False)
    priced=result['price_per_tire'].notna().sum()
    missing=result['MAP'].isna().sum()
    violations=(result['below_map']==True).sum()
    print(f"Saved {out}: {len(result)} variants, {priced} priced, {missing} missing MAP, {violations} below MAP")
    if missing:
        print('Unmatched MAP rows:')
        print(result.loc[result['MAP'].isna(), ['model','size','RAW SIZE']].head(30).to_string(index=False))
    if not priced:
        raise RuntimeError('No prices available for MAP analysis')

if __name__ == '__main__':
    main()
