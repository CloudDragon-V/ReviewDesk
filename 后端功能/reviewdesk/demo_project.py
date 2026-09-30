"""Small cross-module teaching project; imported as data, never executed."""

DEMO_FILES = {
    "main.py": '''"""入口模块：读取购物项，计算订单，输出用户可读的账单。"""
from pricing import calculate_order
from presentation import format_receipt


def main():
    """准备输入，再依次调用计算模块和展示模块。"""
    items = [{"name": "笔记本", "price": 12.5, "quantity": 2},
             {"name": "水笔", "price": 3.0, "quantity": 4}]
    member = True
    order = calculate_order(items, member)
    receipt = format_receipt(order)
    print(receipt)
    return order


if __name__ == "__main__":
    main()
''',
    "pricing.py": '''"""计价模块：验证单价与数量，累计金额，按会员规则折扣。"""


def validate_item(item):
    """要求单价非负、数量为正，否则抛出异常。"""
    if item["price"] < 0:
        raise ValueError("单价不能为负数")
    if item["quantity"] <= 0:
        raise ValueError("数量必须大于零")
    return item["price"] * item["quantity"]


def calculate_order(items, member=False):
    """接收商品列表，返回原价、折扣额、实付额与逐项金额。"""
    subtotal = 0.0
    details = []
    for item in items:
        amount = validate_item(item)
        subtotal += amount
        details.append({"name": item["name"], "amount": amount})
    discount = 0.0
    if member and subtotal >= 30:
        discount = subtotal * 0.1
    total = round(subtotal - discount, 2)
    return {"subtotal": subtotal, "discount": discount,
            "total": total, "details": details}
''',
    "presentation.py": '''"""展示模块：将订单字典转成便于阅读的多行文本。"""


def format_receipt(order):
    """读取金额字段，组织清单，返回文本；不重新计算计价规则。"""
    lines = ["订单清单"]
    for detail in order["details"]:
        lines.append(f"{detail['name']}：{detail['amount']:.2f} 元")
    lines.append(f"原价：{order['subtotal']:.2f} 元")
    lines.append(f"优惠：{order['discount']:.2f} 元")
    lines.append(f"实付：{order['total']:.2f} 元")
    return "\\n".join(lines)
''',
}
