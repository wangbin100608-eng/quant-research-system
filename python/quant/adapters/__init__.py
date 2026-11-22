from .base import BaseAdapter, FetchResult, CANONICAL_SCHEMA
from .fixture import FixtureAdapter
from .fred import FredAdapter
from .sec import SecEdgarAdapter
from .akshare_cn import AkshareCNAdapter
from .coverage import build_coverage_report
from .real_base import RealAdapterBase
from .eastmoney_cn import EastmoneyCNAdapter
from .yahoo_us import YahooUSAdapter
from .fred_v2 import FREDAdapterV2
from .sec_v2 import SECAdapterV2
from .news_v2 import NewsAdapterV2

__all__ = [
    "BaseAdapter", "FetchResult", "CANONICAL_SCHEMA",
    "FixtureAdapter", "FredAdapter", "SecEdgarAdapter",
    "AkshareCNAdapter", "build_coverage_report",
    "RealAdapterBase", "EastmoneyCNAdapter", "YahooUSAdapter",
    "FREDAdapterV2", "SECAdapterV2",
]
