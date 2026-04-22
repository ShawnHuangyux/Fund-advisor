"""akshare 封装：基金基本信息 + 最新单位净值。

采用文件 JSON 缓存（data/cache/），避免每次刷新都走网络。
- 基本信息缓存：7 天
- 最新净值缓存：2 小时（基金净值每日收盘后公布，盘中频繁拉没意义）
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from loguru import logger

# akshare 模块体积大，首次导入较慢；懒加载到函数内。
_CACHE_DIR = Path("data/cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASIC_TTL = timedelta(days=7)
NAV_TTL = timedelta(hours=2)
HISTORY_TTL = timedelta(hours=12)
INDEX_VALUATION_TTL = timedelta(hours=12)


class FundDataError(RuntimeError):
    """akshare 联网失败或基金不存在。"""


def _cache_path(kind: str, code: str) -> Path:
    return _CACHE_DIR / f"{code}_{kind}.json"


def _read_cache(kind: str, code: str, ttl: timedelta) -> dict[str, Any] | None:
    p = _cache_path(kind, code)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存读取失败 {}: {}", p, e)
        return None
    fetched_at = datetime.fromisoformat(payload.get("_fetched_at", "1970-01-01T00:00:00"))
    if datetime.now() - fetched_at > ttl:
        return None
    return payload


def _write_cache(kind: str, code: str, payload: dict[str, Any]) -> None:
    payload = dict(payload, _fetched_at=datetime.now().isoformat(timespec="seconds"))
    p = _cache_path(kind, code)
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def clear_cache(code: str | None = None) -> int:
    """清理缓存，返回删除的文件数。不传 code 则清全部。"""
    patterns = [f"{code}_*.json"] if code else ["*.json"]
    count = 0
    for pat in patterns:
        for p in _CACHE_DIR.glob(pat):
            p.unlink()
            count += 1
    return count


# ---- 基本信息 ----
def get_basic_info(code: str, *, use_cache: bool = True) -> dict[str, Any]:
    """返回 {code, name, fund_type_raw, inception_date, fund_company, fund_manager, latest_scale}。

    任何字段缺失时给 None。联网失败抛 FundDataError。
    """
    code = code.strip().zfill(6)
    if use_cache and (c := _read_cache("basic", code, BASIC_TTL)):
        return c

    import akshare as ak  # 懒加载

    try:
        t0 = time.time()
        df = ak.fund_individual_basic_info_xq(symbol=code)
        logger.info("akshare basic_info({}) 用时 {:.2f}s", code, time.time() - t0)
    except Exception as e:  # noqa: BLE001
        raise FundDataError(f"akshare 查询 {code} 基本信息失败：{e}") from e

    kv = dict(zip(df["item"], df["value"], strict=False))
    if not kv.get("基金名称"):
        raise FundDataError(f"未找到基金 {code}")

    inception = kv.get("成立时间")
    inception_date_str: str | None = None
    if inception and not (isinstance(inception, float)):
        try:
            inception_date_str = str(inception)
        except Exception:  # noqa: BLE001
            inception_date_str = None

    data = {
        "code": code,
        "name": str(kv.get("基金名称", "")).strip(),
        "fund_type_raw": str(kv.get("基金类型", "")).strip(),
        "inception_date": inception_date_str,
        "fund_company": _safe_str(kv.get("基金公司")),
        "fund_manager": _safe_str(kv.get("基金经理")),
        "latest_scale": _safe_str(kv.get("最新规模")),
    }
    _write_cache("basic", code, data)
    return data


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"nan", "<na>", "none"}:
        return None
    return s


# ---- 最新净值 ----
def get_latest_nav(code: str, *, use_cache: bool = True) -> dict[str, Any]:
    """返回 {code, nav: Decimal, nav_date: date, daily_change_pct: Decimal}。"""
    code = code.strip().zfill(6)
    if use_cache and (c := _read_cache("nav", code, NAV_TTL)):
        # 反序列化
        return {
            "code": c["code"],
            "nav": Decimal(c["nav"]),
            "nav_date": date.fromisoformat(c["nav_date"]),
            "daily_change_pct": Decimal(c.get("daily_change_pct", "0")),
        }

    import akshare as ak

    try:
        t0 = time.time()
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        logger.info("akshare nav({}) 用时 {:.2f}s", code, time.time() - t0)
    except Exception as e:  # noqa: BLE001
        raise FundDataError(f"akshare 查询 {code} 净值失败：{e}") from e

    if df is None or df.empty:
        raise FundDataError(f"基金 {code} 无净值数据（可能是封闭/清盘）")

    last = df.iloc[-1]
    nav_date = last["净值日期"]
    if hasattr(nav_date, "date"):
        nav_date = nav_date.date()
    elif isinstance(nav_date, str):
        nav_date = date.fromisoformat(nav_date)

    data = {
        "code": code,
        "nav": Decimal(str(last["单位净值"])),
        "nav_date": nav_date,
        "daily_change_pct": Decimal(str(last.get("日增长率") or 0)),
    }
    _write_cache(
        "nav",
        code,
        {
            "code": data["code"],
            "nav": str(data["nav"]),
            "nav_date": data["nav_date"].isoformat(),
            "daily_change_pct": str(data["daily_change_pct"]),
        },
    )
    return data


# ---- 净值曲线（近 N 年） ----
def get_nav_history(
    code: str,
    *,
    years: int = 3,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """返回按日期升序的净值列表：[{date, nav, daily_change_pct}, ...]。

    默认取近 3 年；不足则全量。货币基金无此接口，返回空列表。
    """
    code = code.strip().zfill(6)
    cache_key = f"nav_hist_{years}y"
    if use_cache and (c := _read_cache(cache_key, code, HISTORY_TTL)):
        return [
            {
                "date": date.fromisoformat(r["date"]),
                "nav": Decimal(r["nav"]),
                "daily_change_pct": Decimal(r.get("daily_change_pct", "0")),
            }
            for r in c.get("rows", [])
        ]

    import akshare as ak

    try:
        t0 = time.time()
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        logger.info("akshare nav_history({}, {}y) 用时 {:.2f}s", code, years, time.time() - t0)
    except Exception as e:  # noqa: BLE001
        raise FundDataError(f"akshare 查询 {code} 净值历史失败：{e}") from e

    if df is None or df.empty:
        raise FundDataError(f"基金 {code} 无净值历史")

    cutoff = date.today() - timedelta(days=365 * years + 5)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        d = row["净值日期"]
        if hasattr(d, "date"):
            d = d.date()
        elif isinstance(d, str):
            d = date.fromisoformat(d)
        if d < cutoff:
            continue
        rows.append(
            {
                "date": d,
                "nav": Decimal(str(row["单位净值"])),
                "daily_change_pct": Decimal(str(row.get("日增长率") or 0)),
            }
        )
    rows.sort(key=lambda r: r["date"])

    _write_cache(
        cache_key,
        code,
        {
            "rows": [
                {
                    "date": r["date"].isoformat(),
                    "nav": str(r["nav"]),
                    "daily_change_pct": str(r["daily_change_pct"]),
                }
                for r in rows
            ]
        },
    )
    return rows


# ---- 指数估值（PE 分位） ----
# 基金名称关键词 → akshare funddb 的指数 symbol（中文名）
_INDEX_KEYWORD_MAP: dict[str, str] = {
    "沪深300": "沪深300",
    "中证300": "沪深300",
    "中证500": "中证500",
    "中证800": "中证800",
    "中证1000": "中证1000",
    "上证50": "上证50",
    "创业板": "创业板指",
    "科创50": "科创50",
    "北证50": "北证50",
    "红利": "中证红利",
    "中证红利": "中证红利",
    "标普500": "标普500",
    "纳斯达克": "纳斯达克100",
    "恒生": "恒生指数",
    "MSCI": "MSCI中国A股",
}


def match_index_symbol(fund_name: str) -> str | None:
    """通过基金名称关键词匹配对应指数（funddb symbol）。无法识别返回 None。"""
    if not fund_name:
        return None
    # 最长优先，避免"沪深300"被"300"误匹配等
    for kw in sorted(_INDEX_KEYWORD_MAP.keys(), key=len, reverse=True):
        if kw in fund_name:
            return _INDEX_KEYWORD_MAP[kw]
    return None


def get_index_valuation(symbol: str, *, use_cache: bool = True) -> dict[str, Any]:
    """返回 {symbol, as_of, pe, pb, pe_percentile}。pe_percentile 为近 3 年内分位（0-1）。

    - symbol 必须是 funddb 指数中文名（见 ``_INDEX_KEYWORD_MAP``）
    - akshare 接口：``index_value_hist_funddb(symbol=..., indicator='市盈率')``
    - 任意字段缺失时置 None；接口失败抛 FundDataError。
    """
    cache_key = f"idx_val_{symbol}"
    if use_cache and (c := _read_cache("idx", cache_key, INDEX_VALUATION_TTL)):
        return {
            "symbol": c["symbol"],
            "as_of": date.fromisoformat(c["as_of"]) if c.get("as_of") else None,
            "pe": Decimal(c["pe"]) if c.get("pe") is not None else None,
            "pb": Decimal(c["pb"]) if c.get("pb") is not None else None,
            "pe_percentile": (
                Decimal(c["pe_percentile"]) if c.get("pe_percentile") is not None else None
            ),
        }

    import akshare as ak

    try:
        t0 = time.time()
        df = ak.index_value_hist_funddb(symbol=symbol, indicator="市盈率")
        logger.info(
            "akshare index_value({}) 用时 {:.2f}s rows={}",
            symbol, time.time() - t0, 0 if df is None else len(df),
        )
    except Exception as e:  # noqa: BLE001
        raise FundDataError(f"akshare 查询指数 {symbol} 估值失败：{e}") from e

    if df is None or df.empty:
        raise FundDataError(f"指数 {symbol} 无估值数据")

    # 列名可能是 日期/市盈率/市净率 或 英文，统一处理
    cols = {c: c for c in df.columns}
    date_col = next((c for c in cols if "日期" in c or c.lower() == "date"), None)
    pe_col = next((c for c in cols if "市盈率" in c or "pe" in c.lower()), None)
    pb_col = next((c for c in cols if "市净率" in c or "pb" in c.lower()), None)
    if date_col is None or pe_col is None:
        raise FundDataError(f"指数 {symbol} 返回字段不符预期：{list(cols)}")

    # 只取近 3 年计算分位
    cutoff = date.today() - timedelta(days=365 * 3 + 5)
    pe_series: list[tuple[date, float]] = []
    for _, row in df.iterrows():
        d = row[date_col]
        if hasattr(d, "date"):
            d = d.date()
        elif isinstance(d, str):
            d = date.fromisoformat(d[:10])
        try:
            pe_v = float(row[pe_col])
        except (TypeError, ValueError):
            continue
        if d >= cutoff:
            pe_series.append((d, pe_v))

    if not pe_series:
        raise FundDataError(f"指数 {symbol} 近 3 年无有效 PE 数据")

    pe_series.sort(key=lambda x: x[0])
    as_of, latest_pe = pe_series[-1]
    vals = [v for _, v in pe_series]
    below_or_eq = sum(1 for v in vals if v <= latest_pe)
    percentile = below_or_eq / len(vals)

    last_row = df.iloc[-1]
    pb_val: float | None = None
    if pb_col is not None:
        try:
            pb_val = float(last_row[pb_col])
        except (TypeError, ValueError):
            pb_val = None

    data = {
        "symbol": symbol,
        "as_of": as_of,
        "pe": Decimal(str(latest_pe)).quantize(Decimal("0.01")),
        "pb": Decimal(str(pb_val)).quantize(Decimal("0.01")) if pb_val is not None else None,
        "pe_percentile": Decimal(str(percentile)).quantize(Decimal("0.0001")),
    }
    _write_cache(
        "idx",
        cache_key,
        {
            "symbol": data["symbol"],
            "as_of": data["as_of"].isoformat(),
            "pe": str(data["pe"]) if data["pe"] is not None else None,
            "pb": str(data["pb"]) if data["pb"] is not None else None,
            "pe_percentile": str(data["pe_percentile"]),
        },
    )
    return data


def enrich_holding_inplace(holding, *, fill_name: bool = True, fill_type: bool = True,
                            fetch_nav: bool = True) -> dict[str, Any]:
    """就地补全 Holding 的 name/fund_type 并填充 latest_nav。

    返回一个字典说明做了什么改动；任何网络失败都会被降级为 warning 并保持原值。
    """
    from ..models.fund import normalize_fund_type

    changes: dict[str, Any] = {"code": holding.code}

    need_basic = (fill_name and not holding.name) or (fill_type and holding.fund_type is None)
    if need_basic:
        try:
            info = get_basic_info(holding.code)
            if fill_name and not holding.name:
                holding.name = info["name"]
                changes["name"] = info["name"]
            if fill_type and holding.fund_type is None:
                holding.fund_type = normalize_fund_type(info.get("fund_type_raw", ""))
                changes["fund_type"] = holding.fund_type.value
        except FundDataError as e:
            logger.warning("{} 基本信息补全失败：{}", holding.code, e)
            changes["basic_error"] = str(e)

    if fetch_nav:
        from ..models import FundType as _FT
        # 货币基金单位净值恒为 1.0，akshare 对货基没有"单位净值走势"接口
        if holding.fund_type == _FT.MONEY:
            from datetime import date as _date
            from decimal import Decimal as _Dec
            holding.latest_nav = _Dec("1.0")
            holding.latest_nav_date = _date.today()
            changes["latest_nav"] = "1.0"
            changes["latest_nav_date"] = _date.today().isoformat()
            changes["note"] = "money_fund: nav fixed to 1.0"
        else:
            try:
                nav = get_latest_nav(holding.code)
                holding.latest_nav = nav["nav"]
                holding.latest_nav_date = nav["nav_date"]
                changes["latest_nav"] = str(nav["nav"])
                changes["latest_nav_date"] = nav["nav_date"].isoformat()
            except FundDataError as e:
                logger.warning("{} 最新净值拉取失败：{}", holding.code, e)
                changes["nav_error"] = str(e)

    return changes
