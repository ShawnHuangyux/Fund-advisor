"""读写 config/portfolio.yaml 与 config/settings.yaml。

写入采用"备份 + 原子替换"策略，防止误写覆盖。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from ..models import Portfolio, Settings

DEFAULT_PORTFOLIO_PATH = Path("config/portfolio.yaml")
DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")


def _yaml_safe_load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        raise ValueError(f"配置文件 {path} 为空")
    if not isinstance(data, dict):
        raise ValueError(f"配置文件 {path} 顶层必须是映射类型")
    return data


def load_portfolio(path: str | os.PathLike[str] = DEFAULT_PORTFOLIO_PATH) -> Portfolio:
    """从 YAML 加载并用 Pydantic 校验 Portfolio。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到持仓配置文件：{p.resolve()}")
    raw = _yaml_safe_load(p)
    return Portfolio.model_validate(raw)


def _portfolio_to_plain(portfolio: Portfolio) -> dict[str, Any]:
    """把 Portfolio 转成 YAML 友好的 dict。

    - Decimal → float；date → 'YYYY-MM-DD'
    - None 值的 name/notes/fund_type 保留为 null，便于下次加载仍触发补全
    - 运行时字段（latest_nav 等，Field(exclude=True)）自动被 model_dump 剔除
    """

    def _convert(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return float(obj)
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert(v) for v in obj]
        return obj

    return _convert(portfolio.model_dump(mode="json", exclude_none=False))


def save_portfolio(
    portfolio: Portfolio,
    path: str | os.PathLike[str] = DEFAULT_PORTFOLIO_PATH,
    *,
    backup: bool = True,
) -> Path:
    """原子写回 portfolio.yaml。写前默认备份旧文件。返回备份文件路径（无备份返回 None 占位）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    backup_path = None
    if backup and p.exists():
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = p.with_name(f"{p.name}.backup-{ts}")
        shutil.copy2(p, backup_path)
        logger.info("portfolio.yaml 备份到 {}", backup_path)

    plain = _portfolio_to_plain(portfolio)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=p.parent,
        prefix=f".{p.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        yaml.safe_dump(plain, tmp, allow_unicode=True, sort_keys=False)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, p)
    logger.info("portfolio.yaml 已写入 {}", p)
    return backup_path if backup_path else p


def load_settings(path: str | os.PathLike[str] = DEFAULT_SETTINGS_PATH) -> Settings:
    """加载 settings.yaml；若文件缺失则返回默认 Settings（便于新装用户起步）。"""
    p = Path(path)
    if not p.exists():
        logger.warning("settings.yaml 不存在（{}），使用内置默认值", p)
        return Settings()
    raw = _yaml_safe_load(p)
    return Settings.model_validate(raw)
