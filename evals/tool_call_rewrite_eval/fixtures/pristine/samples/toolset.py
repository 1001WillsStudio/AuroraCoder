from toolstore.toolset import tool


@tool
def compute(a: float, b: float, op: str = "add"):
    """Run a binary arithmetic operation."""
    ops = {
        "add": a + b,
        "subtract": a - b,
        "multiply": a * b,
        "divide": a / b if b != 0 else float("inf"),
    }
    if op not in ops:
        return {"error": f"Unknown op '{op}'. Use: {list(ops.keys())}"}
    return {"result": ops[op]}


@tool
def convert_temp(value: float, from_unit: str):
    """Convert temperature between Celsius and Fahrenheit."""
    if from_unit == "celsius":
        f = value * 9 / 5 + 32
        return {"celsius": value, "fahrenheit": round(f, 2)}
    elif from_unit == "fahrenheit":
        c = (value - 32) * 5 / 9
        return {"fahrenheit": value, "celsius": round(c, 2)}
    return {"error": "from_unit must be 'celsius' or 'fahrenheit'"}


# Private helper — not @tool, not callable by agents
def _version() -> str:
    return "1.0.0"
