"""Weekly Giti Priority Tire scraper. Uses Priority's Apollo variant data."""
import asyncio
import datetime
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

COLUMN_ORDER = [
    "run_date",
    "source",
    "model",
    "size",
    "original_price",
    "easy_score",
    "reviews",
    "part_number",
    "sku",
    "rating",
    "review_count",
    "in_stock",
    "price_per_tire",
    "total_4_tires",
    "warranty",
    "url",
    "error",
]


def save_checkpoint(rows, filename):
    """
    Save whatever has been collected so far.

    This is deliberately simple so that if GitHub Actions kills the process
    later, we still have partial data available.
    """
    if not rows:
        return

    df = pd.DataFrame(rows)
    df = df.reindex(columns=COLUMN_ORDER)

    for col in [
        "price_per_tire",
        "original_price",
        "total_4_tires",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.to_csv(filename, index=False)


PRIORITY_BASE_URL = (
    "https://www.prioritytire.com"
)

PRIORITY_MAX_RETRY = 2


PRIORITY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,"
        "application/xhtml+xml,"
        "application/xml;q=0.9,"
        "*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


PRIORITY_SEEDS = [
    {"model": "GitiComfort XA1", "url": f"{PRIORITY_BASE_URL}/by-brand/giti-tires/giticomfort-xa1/215-65r17-99h-vw-380754"},
    {"model": "WeatherSmart", "url": f"{PRIORITY_BASE_URL}/by-brand/giti-tires/weathersmart/giti-weathersmart-235-60r18-103v-n1682484-99"},
    {"model": "GitiXCursion XT", "url": f"{PRIORITY_BASE_URL}/by-brand/giti-tires/gitixcursion-xt/giti-gitixcursion-xt-lt-315-70r17-126-123s-e-10-ply"},
    {"model": "GitiXCursion AT", "url": f"{PRIORITY_BASE_URL}/by-brand/giti-tires/gitixcursion-at/giti-gitixcursion-at-265-50r20-111t-xl"},
]


def _priority_product_path(url):
    parts = (
        url
        .replace(
            PRIORITY_BASE_URL,
            "",
        )
        .strip("/")
        .split("/")
    )

    return "/" + "/".join(
        parts[:3]
    )


async def priority_get_size_urls(
    page,
    seed,
):
    model_name = seed["model"]
    seed_url = seed["url"]

    seed_product_path = (
        _priority_product_path(seed_url)
    )

    print(
        f"\n[PRIORITY] ── "
        f"{model_name}"
    )

    print(
        f"  Loading seed: "
        f"{seed_url}"
    )

    await page.goto(
        seed_url,
        wait_until="domcontentloaded",
        timeout=30_000,
    )

    await page.wait_for_selector(
        "script#__NEXT_DATA__",
        state="attached",
        timeout=15_000,
    )

    raw = await page.evaluate(
        """() => {
            const el =
                document.getElementById(
                    '__NEXT_DATA__'
                );

            return el
                ? el.textContent
                : null;
        }"""
    )

    if not raw:
        print(
            "  ⚠️ __NEXT_DATA__ "
            "not found — skipping."
        )
        return []

    nd = json.loads(raw)

    apollo = (
        nd["props"]
        ["pageProps"]
        ["apolloState"]
    )

    config_product = next(
        (
            value
            for key, value
            in apollo.items()
            if (
                key.startswith(
                    "ConfigurableProduct:"
                )
                and isinstance(
                    value,
                    dict,
                )
                and "variants" in value
            )
        ),
        None,
    )

    if not config_product:
        print(
            "  ⚠️ No ConfigurableProduct "
            "found — skipping."
        )
        return []

    print(
        f"  Found "
        f"{len(config_product['variants'])} "
        f"variants in apolloState"
    )

    variants = []

    for variant in config_product[
        "variants"
    ]:
        if not isinstance(
            variant,
            dict,
        ):
            continue

        size_label = None

        for attr in (
            variant.get(
                "attributes"
            )
            or []
        ):
            if (
                isinstance(attr, dict)
                and attr.get("__ref")
            ):
                attr = apollo.get(
                    attr["__ref"],
                    {},
                )

            label = (
                attr.get("label")
                or attr.get(
                    "store_label"
                )
            )

            if label:
                size_label = label
                break

        prod_ref = (
            variant.get("product")
            or {}
        )

        if (
            isinstance(prod_ref, dict)
            and prod_ref.get("__ref")
        ):
            simple = apollo.get(
                prod_ref["__ref"],
                {},
            )
        else:
            simple = prod_ref

        sku_id = (
            simple.get("id")
            or simple.get("uid")
        )

        slug = None

        for rewrite in (
            simple.get("url_rewrites")
            or []
        ):
            if (
                isinstance(
                    rewrite,
                    dict,
                )
                and rewrite.get(
                    "__ref"
                )
            ):
                rewrite = apollo.get(
                    rewrite["__ref"],
                    {},
                )

            if (
                isinstance(
                    rewrite,
                    dict,
                )
                and rewrite.get("url")
            ):
                slug = (
                    "/"
                    + rewrite["url"]
                    .lstrip("/")
                )
                break

        if (
            not slug
            and size_label
            and sku_id
        ):
            size_slug = re.sub(
                r"-{2,}",
                "-",
                re.sub(
                    r"[^a-z0-9]",
                    "-",
                    size_label.lower(),
                ),
            ).strip("-")

            slug = (
                f"{seed_product_path}/"
                f"{size_slug}-"
                f"{sku_id}"
            )

        if slug:
            variants.append(
                {
                    "model": model_name,
                    "size": (
                        size_label
                        or slug.split(
                            "/"
                        )[-1]
                    ),
                    "url": (
                        PRIORITY_BASE_URL
                        + slug
                    ),
                    "sku": (
                        str(sku_id)
                        if sku_id
                        else None
                    ),
                    "in_stock": (
                        simple.get(
                            "stock_status"
                        )
                        == "IN_STOCK"
                        if "stock_status"
                        in simple
                        else None
                    ),
                }
            )

    seen = set()
    deduped = []

    for variant in variants:

        if variant["url"] in seen:
            continue

        seen.add(
            variant["url"]
        )

        deduped.append(
            variant
        )

    print(
        f"  → {len(deduped)} "
        f"unique sizes ready to scrape"
    )

    return deduped


def priority_parse_next_data(
    html,
    fallback_size,
    url=None,
    sku=None,
):
    result = {
        "part_number": None,
        "price_per_tire": None,
        "total_4_tires": None,
        "rating": None,
        "review_count": None,
        "warranty": None,
        "in_stock": None,
        "size": fallback_size,
    }

    match = re.search(
        (
            r'<script '
            r'id="__NEXT_DATA__"'
            r'[^>]*>'
            r'(.*?)'
            r'</script>'
        ),
        html,
        re.DOTALL,
    )

    if not match:
        return result

    try:
        nd = json.loads(
            match.group(1)
        )

        apollo = (
            nd["props"]
            ["pageProps"]
            ["apolloState"]
        )

        # The Apollo SimpleProduct ID discovered during the seed
        # crawl is more reliable than trying to guess from the URL.
        target_id = (
            str(sku)
            if sku is not None
            else None
        )

        if (
            target_id is None
            and url
        ):
            last_segment = (
                url.rstrip("/")
                .split("/")[-1]
            )

            id_match = re.search(
                r"-(\d+)$",
                last_segment,
            )

            if id_match:
                target_id = (
                    id_match.group(1)
                )

        simple = None

        for key, value in (
            apollo.items()
        ):
            if (
                key.startswith(
                    "SimpleProduct:"
                )
                and isinstance(
                    value,
                    dict,
                )
                and "price_range"
                in value
            ):
                product_id = (
                    value.get("id")
                    or value.get("uid")
                )

                if (
                    target_id is None
                    or str(
                        product_id
                    )
                    == target_id
                ):
                    simple = value
                    break

        config = next(
            (
                value
                for key, value
                in apollo.items()
                if (
                    key.startswith(
                        "ConfigurableProduct:"
                    )
                    and isinstance(
                        value,
                        dict,
                    )
                )
            ),
            None,
        )

        if simple:
            # Priority displays this identifier as "MPN". Apollo field names
            # can vary, so try the known variants before using the page text.
            result["part_number"] = (
                simple.get("mpn")
                or simple.get("manufacturer_part_number")
                or simple.get("manufacturerPartNumber")
            )
            try:
                price_range = (
                    simple["price_range"]
                )

                if (
                    isinstance(
                        price_range,
                        dict,
                    )
                    and price_range.get(
                        "__ref"
                    )
                ):
                    price_range = (
                        apollo.get(
                            price_range[
                                "__ref"
                            ],
                            {},
                        )
                    )

                minimum_price = (
                    price_range.get(
                        "minimum_price"
                    )
                    or {}
                )

                if (
                    isinstance(
                        minimum_price,
                        dict,
                    )
                    and minimum_price.get(
                        "__ref"
                    )
                ):
                    minimum_price = (
                        apollo.get(
                            minimum_price[
                                "__ref"
                            ],
                            {},
                        )
                    )

                final_price = (
                    minimum_price.get(
                        "final_price"
                    )
                    or {}
                )

                if (
                    isinstance(
                        final_price,
                        dict,
                    )
                    and final_price.get(
                        "__ref"
                    )
                ):
                    final_price = (
                        apollo.get(
                            final_price[
                                "__ref"
                            ],
                            {},
                        )
                    )

                price_value = (
                    final_price.get(
                        "value"
                    )
                )

                if price_value is not None:
                    result[
                        "price_per_tire"
                    ] = float(
                        price_value
                    )

                    result[
                        "total_4_tires"
                    ] = round(
                        float(
                            price_value
                        )
                        * 4,
                        2,
                    )

            except Exception:
                pass

            if simple.get(
                "stock_status"
            ):
                result[
                    "in_stock"
                ] = (
                    simple[
                        "stock_status"
                    ]
                    == "IN_STOCK"
                )

        if config:
            rating_data = (
                config.get(
                    "productRating"
                )
                or {}
            )

            if (
                isinstance(
                    rating_data,
                    dict,
                )
                and rating_data.get(
                    "__ref"
                )
            ):
                rating_data = (
                    apollo.get(
                        rating_data[
                            "__ref"
                        ],
                        {},
                    )
                )

            rating_value = (
                rating_data.get(
                    "rating_summary"
                )
                or config.get(
                    "rating_summary"
                )
            )

            if rating_value:
                try:
                    result[
                        "rating"
                    ] = (
                        f"{float(rating_value) / 20:.1f}/5"
                    )

                except Exception:
                    result[
                        "rating"
                    ] = str(
                        rating_value
                    )

            if (
                config.get(
                    "review_count"
                )
                is not None
            ):
                result[
                    "review_count"
                ] = str(
                    config[
                        "review_count"
                    ]
                )

            if config.get(
                "treadlife_warranty_text"
            ):
                result[
                    "warranty"
                ] = config[
                    "treadlife_warranty_text"
                ]

        soup = BeautifulSoup(
            html,
            "lxml",
        )

        if not result["part_number"]:
            mpn_label = soup.find(
                lambda tag: tag.name in {"span", "p", "div"}
                and tag.get_text(" ", strip=True).upper() == "MPN"
            )
            if mpn_label:
                # The value is normally the next element in the same details
                # row; fall back to the parent's remaining text.
                value_element = mpn_label.find_next_sibling()
                if value_element:
                    value = value_element.get_text(" ", strip=True)
                else:
                    parent_text = mpn_label.parent.get_text(" ", strip=True)
                    value = re.sub(r"^MPN\s*[:#-]?\s*", "", parent_text, flags=re.I)
                if value and value.upper() != "MPN":
                    result["part_number"] = value.strip()

        if (
            result[
                "price_per_tire"
            ]
            is None
        ):
            el = soup.select_one(
                ".ProductPagePrice-finalPrice span"
            )

            if el:
                try:
                    price = float(
                        el
                        .get_text(
                            strip=True
                        )
                        .replace(
                            "$",
                            "",
                        )
                        .replace(
                            ",",
                            "",
                        )
                    )

                    result[
                        "price_per_tire"
                    ] = price

                    result[
                        "total_4_tires"
                    ] = round(
                        price * 4,
                        2,
                    )

                except ValueError:
                    pass

        if (
            result["in_stock"]
            is None
        ):
            page_text = " ".join(
                soup.stripped_strings
            )

            if re.search(
                r"\bIn Stock\b",
                page_text,
                re.IGNORECASE,
            ):
                result[
                    "in_stock"
                ] = True

            elif re.search(
                r"\bOut of Stock\b",
                page_text,
                re.IGNORECASE,
            ):
                result[
                    "in_stock"
                ] = False

    except Exception as e:
        result[
            "parse_error"
        ] = str(e)

    return result


async def priority_fetch_one(
    page,
    item,
    run_date,
    index,
    total,
):
    size = item["size"]
    url = item["url"]

    print(
        f"  Loading "
        f"[{index:03}/{total}] "
        f"{item.get('model'):22s}  "
        f"{str(size):28s}",
        flush=True,
    )

    result = {
        "run_date": run_date,
        "source": "priority",
        "model": item.get("model"),
        "size": size,
        "original_price": None,
        "easy_score": None,
        "reviews": None,
        "part_number": None,
        "sku": item.get("sku"),
        "rating": None,
        "review_count": None,
        "in_stock": item.get(
            "in_stock"
        ),
        "price_per_tire": None,
        "total_4_tires": None,
        "warranty": None,
        "url": url,
        "error": "",
    }

    for attempt in range(
        1,
        PRIORITY_MAX_RETRY + 1,
    ):
        try:
            await page.goto(
                url,
                wait_until=(
                    "domcontentloaded"
                ),
                timeout=30_000,
            )

            await page.wait_for_selector(
                "script#__NEXT_DATA__",
                state="attached",
                timeout=10_000,
            )

            selected_id = (
                await page.evaluate(
                    """() => {
                        const el =
                            document.getElementById(
                                '__NEXT_DATA__'
                            );

                        if (!el) {
                            return null;
                        }

                        const nd =
                            JSON.parse(
                                el.textContent
                            );

                        const apollo =
                            nd?.props
                              ?.pageProps
                              ?.apolloState
                            || {};

                        for (
                            const [key, value]
                            of Object.entries(apollo)
                        ) {
                            if (
                                key.startsWith(
                                    'SimpleProduct:'
                                )
                                &&
                                value
                                &&
                                typeof value
                                    === 'object'
                                &&
                                value.price_range
                            ) {
                                return String(
                                    value.id
                                    ||
                                    value.uid
                                    ||
                                    ''
                                );
                            }
                        }

                        return null;
                    }"""
                )
            )

            expected_id = (
                str(
                    item.get("sku")
                )
                if item.get("sku")
                else None
            )

            result["url"] = (
                page.url
            )

            # Some obsolete variants redirect to another
            # product. Don't save that product under the
            # requested size.
            if (
                expected_id
                and selected_id
                and selected_id
                != expected_id
            ):
                result[
                    "in_stock"
                ] = False

                result[
                    "error"
                ] = (
                    f"Variant "
                    f"{expected_id} "
                    f"redirected to "
                    f"{selected_id}; "
                    f"skipped"
                )

                break

            html = (
                await page.content()
            )

            parsed = (
                priority_parse_next_data(
                    html,
                    size,
                    url=page.url,
                    sku=expected_id,
                )
            )

            result.update(parsed)

            if (
                result.get(
                    "price_per_tire"
                )
                is None
            ):
                result[
                    "error"
                ] = (
                    "Price not found "
                    "after browser navigation"
                )

            break

        except PlaywrightTimeout as e:

            if (
                attempt
                < PRIORITY_MAX_RETRY
            ):
                print(
                    f"    retry "
                    f"{attempt}..."
                )

                await asyncio.sleep(
                    2 * attempt
                )

            else:
                result[
                    "error"
                ] = (
                    f"Timeout after "
                    f"{PRIORITY_MAX_RETRY} "
                    f"attempts: {e}"
                )

        except Exception as e:
            result[
                "error"
            ] = (
                f"Browser scrape "
                f"error: {e}"
            )

            break

    price = (
        f"${result.get('price_per_tire')}"
        if result.get(
            "price_per_tire"
        )
        else "$N/A"
    )

    stock = (
        "In Stock"
        if result.get(
            "in_stock"
        )
        else "OOS"
    )

    err = (
        f"  !! {result['error']}"
        if result.get("error")
        else ""
    )

    print(
        f"  "
        f"[{index:03}/{total}] "
        f"{result['model']:22s}  "
        f"{str(result['size']):28s}  "
        f"{price:>10}  "
        f"{stock}"
        f"{err}"
    )

    return result


async def run_priority(
    run_date,
):
    all_size_links = []

    print(
        "\n"
        + "═" * 60
    )

    print(
        "  PRIORITY TIRE"
    )

    print(
        "═" * 60
    )

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        ctx = await browser.new_context(
            user_agent=(
                PRIORITY_HEADERS[
                    "User-Agent"
                ]
            )
        )

        discovery_page = (
            await ctx.new_page()
        )

        detail_page = (
            await ctx.new_page()
        )

        # Block known third-party junk.
        async def block_priority(
            route,
        ):
            url = (
                route.request.url
                .lower()
            )

            blocked = [
                "attentive",
                "attn.tv",
                "google-analytics",
                "googletagmanager",
                "doubleclick",
                "facebook",
            ]

            if any(
                term in url
                for term in blocked
            ):
                await route.abort()
            else:
                await route.continue_()

        await discovery_page.route(
            "**/*",
            block_priority,
        )

        await detail_page.route(
            "**/*",
            block_priority,
        )

        # --------------------------------------------------------------
        # DISCOVER ALL VARIANTS
        # --------------------------------------------------------------

        for seed in PRIORITY_SEEDS:

            try:
                links = (
                    await priority_get_size_urls(
                        discovery_page,
                        seed,
                    )
                )

                all_size_links.extend(
                    links
                )

            except Exception as e:
                print(
                    f"  ❌ Priority "
                    f"seed failed for "
                    f"{seed['model']}: "
                    f"{e}"
                )

        total = len(
            all_size_links
        )

        print(
            f"\n  Total Priority "
            f"sizes: {total}\n"
        )

        results = []

        # --------------------------------------------------------------
        # SCRAPE VARIANTS
        # --------------------------------------------------------------

        for i, item in enumerate(
            all_size_links,
            1,
        ):
            try:
                result = (
                    await priority_fetch_one(
                        detail_page,
                        item,
                        run_date,
                        i,
                        total,
                    )
                )

            except Exception as e:

                result = {
                    "run_date": run_date,
                    "source": "priority",
                    "model": item.get(
                        "model"
                    ),
                    "size": item.get(
                        "size"
                    ),
                    "original_price": None,
                    "easy_score": None,
                    "reviews": None,
                    "part_number": None,
                    "sku": item.get(
                        "sku"
                    ),
                    "rating": None,
                    "review_count": None,
                    "in_stock": (
                        item.get(
                            "in_stock"
                        )
                    ),
                    "price_per_tire": None,
                    "total_4_tires": None,
                    "warranty": None,
                    "url": item.get(
                        "url"
                    ),
                    "error": (
                        f"Unhandled "
                        f"scrape error: "
                        f"{e}"
                    ),
                }

            results.append(
                result
            )

            # Save every 25 records.
            if (
                i % 25 == 0
                or i == total
            ):
                save_checkpoint(
                    results,
                    "priority_checkpoint.csv",
                )

                print(
                    f"  💾 Priority "
                    f"checkpoint saved "
                    f"({len(results)} rows)"
                )

            await asyncio.sleep(
                0.3
            )

        await browser.close()

    print(
        f"\n  Priority: "
        f"{len(results)} "
        f"rows collected."
    )

    return results


async def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    run_date = now.strftime("%Y-%m-%d %H:%M UTC")
    date_str = now.strftime("%Y-%m-%d")
    print(f"Giti Priority Tire weekly scrape: {run_date}", flush=True)
    results = await run_priority(run_date)
    save_checkpoint(results, "priority_checkpoint.csv")
    if not results:
        print("ERROR: No Priority Tire variants discovered.", file=sys.stderr)
        sys.exit(1)
    df = pd.DataFrame(results).reindex(columns=COLUMN_ORDER)
    for col in ("price_per_tire", "original_price", "total_4_tires"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    output = f"priority_giti_raw_{date_str}.csv"
    df.to_csv(output, index=False)
    print(f"Raw results saved: {output} | total={len(df)} priced={df['price_per_tire'].notna().sum()}")
    if df['price_per_tire'].notna().sum() == 0:
        print("ERROR: No prices were scraped.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
