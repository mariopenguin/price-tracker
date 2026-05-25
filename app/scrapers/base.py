from dataclasses import dataclass
from typing import Optional


@dataclass
class ScrapeResult:
    price: float
    currency: str
    name: str
    image_url: Optional[str]
