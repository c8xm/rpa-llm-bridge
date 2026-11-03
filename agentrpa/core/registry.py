"""驱动注册表：新增一个驱动 = 实现 BaseDriver + 注册，不触碰 runner。"""

from __future__ import annotations

from typing import Callable

from ..drivers.base import BaseDriver

_REGISTRY: dict[str, Callable[..., BaseDriver]] = {}


def register(name: str):
    def deco(factory: Callable[..., BaseDriver]):
        _REGISTRY[name] = factory
        return factory

    return deco


def create(name: str, *args, **kwargs) -> BaseDriver:
    if name not in _REGISTRY:
        raise KeyError(f"unknown driver: {name}; available={sorted(_REGISTRY)}")
    return _REGISTRY[name](*args, **kwargs)


def names() -> list[str]:
    return sorted(_REGISTRY)


def clear() -> None:
    """仅测试使用。"""
    _REGISTRY.clear()
