"""
辅助函数模块
"""
import time
from decimal import Decimal, ROUND_DOWN
from typing import Union


def round_to_tick(price: Union[Decimal, float, str], tick_size: Union[Decimal, float, str]) -> Decimal:
    """
    将价格取整到 tick 的倍数

    Args:
        price: 原始价格
        tick_size: tick 大小

    Returns:
        取整后的价格
    """
    price = Decimal(str(price))
    tick_size = Decimal(str(tick_size))

    return (price / tick_size).quantize(Decimal('1'), rounding=ROUND_DOWN) * tick_size


def generate_client_order_id(prefix: str = 'arb') -> str:
    """
    生成客户端订单 ID

    Args:
        prefix: 前缀

    Returns:
        唯一的订单 ID
    """
    timestamp = int(time.time() * 1000)
    return f"{prefix}_{timestamp}"


def format_quantity(quantity: Union[Decimal, float, str], precision: int = 8) -> str:
    """
    格式化数量

    Args:
        quantity: 数量
        precision: 小数位数

    Returns:
        格式化后的字符串
    """
    quantity = Decimal(str(quantity))
    format_str = f"1.{'0' * precision}"
    return str(quantity.quantize(Decimal(format_str), rounding=ROUND_DOWN))


def format_price(price: Union[Decimal, float, str], tick_size: Union[Decimal, float, str]) -> str:
    """
    格式化价格 (根据 tick_size 确定精度)

    Args:
        price: 价格
        tick_size: tick 大小

    Returns:
        格式化后的字符串
    """
    price = round_to_tick(price, tick_size)

    # 确定小数位数
    tick_str = str(tick_size)
    if '.' in tick_str:
        decimals = len(tick_str.split('.')[1])
    else:
        decimals = 0

    format_str = f"1.{'0' * decimals}"
    return str(price.quantize(Decimal(format_str)))


def calculate_pnl(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    is_long: bool
) -> Decimal:
    """
    计算盈亏

    Args:
        entry_price: 入场价格
        exit_price: 出场价格
        quantity: 数量
        is_long: 是否做多

    Returns:
        盈亏金额
    """
    if is_long:
        return (exit_price - entry_price) * quantity
    else:
        return (entry_price - exit_price) * quantity
