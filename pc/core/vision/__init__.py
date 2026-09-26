"""Визуальная верификация вёрстки: скриншоты и Vision-аудит через мультимодалку."""

from core.vision.audit import audit_screenshot, describe_image
from core.vision.screenshot import VIEWPORTS, capture_screenshot

__all__ = ["VIEWPORTS", "audit_screenshot", "capture_screenshot", "describe_image"]
